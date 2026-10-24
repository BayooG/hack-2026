from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from google.auth.exceptions import RefreshError

import agent
import gcal
import labels
import run_context
import sublabel_cleanup

from . import cleanup_runner, google_connect, runner
from .forms import LABELS_MAX
from .models import GoogleConnection, Label, ProcessedEmail, SublabelReview, User


def make_connected_user(email="a@example.com"):
    user = User.objects.create_user(email, "a-long-password")
    GoogleConnection.objects.create(
        user=user, google_email=email, refresh_token=google_connect.encrypt("refresh")
    )
    return user


class AccountViewsTests(TestCase):
    def test_health(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_landing_page_adapts_to_login_state(self):
        self.assertContains(self.client.get(reverse("home")), reverse("signup"))
        self.client.force_login(User.objects.create_user("a@example.com", "a-long-password"))
        self.assertContains(self.client.get(reverse("home")), "Open dashboard")

    def test_signup_logs_in_and_rejects_duplicate_email_in_any_case(self):
        data = {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "New@Example.com",
            "password1": "a-long-password",
            "password2": "a-long-password",
        }
        response = self.client.post(reverse("signup"), data)
        self.assertRedirects(response, reverse("dashboard"))
        user = User.objects.get(email="new@example.com")
        self.assertEqual((user.first_name, user.last_name), ("Ada", "Lovelace"))

        self.client.logout()
        response = self.client.post(reverse("signup"), {**data, "email": "NEW@example.com"})
        self.assertContains(response, "already exists")

    def test_signup_requires_first_and_last_name(self):
        data = {"email": "new@example.com", "password1": "a-long-password", "password2": "a-long-password"}
        response = self.client.post(reverse("signup"), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="new@example.com").exists())

    def test_login_is_case_insensitive(self):
        User.objects.create_user("a@example.com", "a-long-password")
        response = self.client.post(reverse("login"), {"username": "A@Example.com", "password": "a-long-password"})
        self.assertRedirects(response, reverse("dashboard"))

    def test_settings_reject_end_before_start(self):
        user = make_connected_user()
        self.client.force_login(user)
        response = self.client.post(reverse("settings"), {"work_start": "18:00", "work_end": "09:00"})
        self.assertContains(response, "must end after")
        user.refresh_from_db()
        self.assertEqual(user.work_start, time(9, 0))

    def test_settings_save_selected_days(self):
        user = make_connected_user()
        self.client.force_login(user)
        data = {"work_start": "08:00", "work_end": "16:00", "work_days": ["0", "2", "5"]}
        self.assertRedirects(self.client.post(reverse("settings"), data), reverse("dashboard"))
        user.refresh_from_db()
        self.assertEqual(user.work_days, [0, 2, 5])
        self.assertEqual(user.available_weekdays(), (0, 2, 5))

    def test_settings_require_a_day_unless_all_days(self):
        user = make_connected_user()
        self.client.force_login(user)
        hours = {"work_start": "09:00", "work_end": "17:00"}
        self.assertContains(self.client.post(reverse("settings"), hours), "Pick at least one day")
        self.client.post(reverse("settings"), {**hours, "all_days": "on"})
        user.refresh_from_db()
        self.assertTrue(user.all_days)
        self.assertEqual(user.available_weekdays(), tuple(range(7)))

    def test_anytime_ignores_hours_order(self):
        user = make_connected_user()
        self.client.force_login(user)
        data = {"work_start": "18:00", "work_end": "09:00", "work_days": ["1"], "anytime": "on"}
        self.assertRedirects(self.client.post(reverse("settings"), data), reverse("dashboard"))
        user.refresh_from_db()
        self.assertTrue(user.anytime)

    def test_connect_explains_missing_google_client(self):
        self.client.force_login(User.objects.create_user("a@example.com", "a-long-password"))
        with self.settings(GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET=""):
            response = self.client.get(reverse("google_connect"), follow=True)
        self.assertContains(response, "isn&#x27;t set up yet")

    def test_connect_redirects_to_google(self):
        self.client.force_login(User.objects.create_user("a@example.com", "a-long-password"))
        with self.settings(GOOGLE_CLIENT_ID="id.apps.googleusercontent.com", GOOGLE_CLIENT_SECRET="secret"):
            response = self.client.get(reverse("google_connect"))
        self.assertTrue(response["Location"].startswith("https://accounts.google.com/o/oauth2/auth?"))
        self.assertIn("client_id=id.apps.googleusercontent.com", response["Location"])
        self.assertIn("state", self.client.session["google_oauth"])

    def test_run_now_needs_a_connection(self):
        user = User.objects.create_user("a@example.com", "a-long-password")
        self.client.force_login(user)
        self.client.post(reverse("run_now"))
        user.refresh_from_db()
        self.assertIsNone(user.run_requested_at)

        GoogleConnection.objects.create(user=user, google_email="a@example.com", refresh_token="x")
        self.client.post(reverse("run_now"))
        user.refresh_from_db()
        self.assertIsNotNone(user.run_requested_at)


@mock.patch.object(runner.google_connect, "credentials", return_value=None)
class RunnerTests(TestCase):
    def setUp(self):
        self.user = make_connected_user()
        patcher = mock.patch.multiple(
            runner.gmail,
            list_labels=mock.Mock(return_value=[{"name": "Work"}]),
            list_inbox_ids=mock.Mock(return_value=["m3", "m2", "m1"]),  # newest first
            get_email=lambda message_id: {
                "id": message_id, "thread_id": f"t{message_id}", "sender": "s", "to": "t", "cc": "",
                "subject": f"subject {message_id}", "body": "b",
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_processes_only_new_emails_oldest_first(self, _):
        ProcessedEmail.objects.create(user=self.user, message_id="m1")
        seen = []
        result = SimpleNamespace(
            labels=[
                SimpleNamespace(label="Work", sublabel_needed=True, sublabel="Launch"),
                SimpleNamespace(label="Team", sublabel_needed=False, sublabel=None),
            ],
            urgency="not_urgent",
        )
        with mock.patch.object(runner.agent, "run", lambda email, labels: seen.append(email.id) or result):
            runner.run_user(self.user)
        self.assertEqual(seen, ["m2", "m3"])
        self.assertEqual(self.user.processed_emails.filter(status="ok").count(), 3)
        self.assertEqual(self.user.processed_emails.get(message_id="m3").label_list, ["Work/Launch", "Team"])
        self.assertEqual(self.user.google.last_error, "")

    def test_run_now_reruns_the_newest_emails_even_if_done(self, _):
        ProcessedEmail.objects.create(user=self.user, message_id="m1", subject="old")
        seen = []
        result = SimpleNamespace(labels=[], urgency="not_urgent")
        with mock.patch.object(runner.agent, "run", lambda email, labels: seen.append(email.id) or result):
            runner.run_user(self.user, rerun_newest=3)
        runner.gmail.list_inbox_ids.assert_called_once_with(3)  # no connected_at cutoff
        self.assertEqual(seen, ["m1", "m2", "m3"])
        self.assertEqual(self.user.processed_emails.get(message_id="m1").subject, "subject m1")

    def test_reply_skill_uses_the_users_name(self, _):
        self.user.first_name, self.user.last_name = "Ada", "Lovelace"
        skills = []
        with mock.patch.object(runner.agent, "run", lambda email, labels: skills.append(runner.agent.load_skill("write-reply"))):
            runner.run_user(self.user)
        self.assertIn('Sign off with "Ada" for casual emails, "Ada Lovelace" for formal ones.', skills[0])

    def test_classifier_sees_the_users_label_descriptions(self, _):
        Label.objects.create(user=self.user, name="Work", sublabels="never", include="Projects at the office.")
        menus = []
        with mock.patch.object(runner.agent, "run", lambda email, names: menus.append(agent.label_menu(names)[1])):
            runner.run_user(self.user)
        self.assertEqual(menus[0], "- Work: Projects at the office. Sub-labels: never.")

    def test_failed_email_is_recorded_and_not_retried(self, _):
        with mock.patch.object(runner.agent, "run", side_effect=ValueError("bad email")):
            runner.run_user(self.user)
        self.assertEqual(self.user.processed_emails.filter(status="error").count(), 3)

    def test_expired_google_access_asks_to_reconnect(self, _):
        runner.gmail.list_labels.side_effect = RefreshError("invalid_grant")
        runner.run_user(self.user)
        self.user.google.refresh_from_db()
        self.assertEqual(self.user.google.status, GoogleConnection.Status.RECONNECT_NEEDED)
        self.assertFalse(self.user.processed_emails.exists())



def formset_data(rows, initial=0):
    """POST data for the labels formset: rows of {field: value}, the first `initial` existing."""
    data = {
        "labels-TOTAL_FORMS": str(len(rows)), "labels-INITIAL_FORMS": str(initial),
        "labels-MIN_NUM_FORMS": "0", "labels-MAX_NUM_FORMS": "1000",
    }
    for i, row in enumerate(rows):
        for field, value in row.items():
            data[f"labels-{i}-{field}"] = value
    return data


@mock.patch.object(runner.google_connect, "credentials", return_value=None)
class LabelsTabTests(TestCase):
    def setUp(self):
        self.user = make_connected_user()
        self.client.force_login(self.user)
        patcher = mock.patch.object(
            runner.gmail, "list_labels",
            return_value=[{"name": "✈️ Travel"}, {"name": "✈️ Travel/Rome"}, {"name": "Work"}],
        )
        self.list_labels = patcher.start()
        self.addCleanup(patcher.stop)

    def rows(self):
        return [(label.emoji, label.name) for label in self.user.labels.all()]

    def test_existing_user_starts_from_labels_md(self, _):
        response = self.client.get(reverse("labels"))
        auth = self.user.labels.get(name="Authorisations")
        self.assertEqual(auth.emoji, "🔐")
        self.assertEqual(auth.sublabels, "never")
        self.assertTrue(auth.include.startswith("Things you act on to get or grant access"))
        self.assertEqual(auth.exclude, "Not warnings about your account (Alerts).")
        finance = self.user.labels.get(name="Finance")
        self.assertTrue(finance.exclude.endswith("not health insurance (Medical)."))
        self.assertEqual(self.user.labels.get(name="University").cleanup, "after the course's exam period; never the thesis")
        self.assertEqual(self.user.labels.count(), len(labels.parse(labels.LABELS_FILE.read_text())))
        self.assertContains(response, "In Gmail but not described: Work")
        self.assertContains(response, "Not in Gmail")

    def test_signup_gets_the_default_labels(self, _):
        self.client.logout()
        self.client.post(reverse("signup"), {
            "first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.com",
            "password1": "a-long-password", "password2": "a-long-password",
        })
        self.assertTrue(User.objects.get(email="ada@example.com").labels.filter(name="Travel").exists())

    def test_edit_add_and_remove(self, _):
        self.user.labels.all().delete()
        travel = Label.objects.create(user=self.user, emoji="✈️", name="Travel", include="Flights.")
        work = Label.objects.create(user=self.user, position=1, name="Work")
        data = formset_data([
            {"id": travel.pk, "emoji": "✈️", "name": "Travel", "sublabels": "one per trip", "include": "Flights and hotels.", "exclude": ""},
            {"id": work.pk, "emoji": "", "name": "Work", "sublabels": "", "include": "", "exclude": "", "DELETE": "on"},
            {"emoji": "", "name": "🧾 Receipts", "sublabels": "never", "include": "Receipts.", "exclude": "Not invoices."},
        ], initial=2)
        response = self.client.post(reverse("labels"), data, follow=True)
        self.assertContains(response, "Labels saved.")
        self.assertEqual(self.rows(), [("✈️", "Travel"), ("🧾", "Receipts")])
        self.assertEqual(self.user.labels.get(name="Travel").sublabels, "one per trip")

    def test_rejects_duplicates_and_slashes(self, _):
        self.user.labels.all().delete()
        Label.objects.create(user=self.user, name="Mine")
        data = formset_data([
            {"name": "Travel"}, {"name": "✈️ travel"}, {"name": "Travel/Rome"},
        ])
        response = self.client.post(reverse("labels"), data)
        self.assertContains(response, "Another label has this name.")
        self.assertContains(response, "No / in label names")
        self.assertEqual(self.rows(), [("", "Mine")])

    def test_rejects_too_many_labels(self, _):
        self.user.labels.all().delete()
        Label.objects.create(user=self.user, name="Mine")
        names = [f"Label {chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(LABELS_MAX + 1)]
        response = self.client.post(reverse("labels"), formset_data([{"name": name} for name in names]))
        self.assertContains(response, f"Please submit at most {LABELS_MAX} forms.")
        self.assertEqual(self.rows(), [("", "Mine")])

    def test_add_missing_gmail_labels(self, _):
        self.user.labels.all().delete()
        Label.objects.create(user=self.user, name="Travel")
        self.client.post(reverse("add_missing_labels"))
        self.assertEqual(self.rows(), [("", "Travel"), ("", "Work")])

    def test_reset_to_defaults(self, _):
        self.user.labels.all().delete()
        Label.objects.create(user=self.user, name="Mine")
        self.client.post(reverse("reset_labels"))
        self.assertFalse(self.user.labels.filter(name="Mine").exists())
        self.assertTrue(self.user.labels.filter(name="University").exists())

    def test_works_without_google(self, _):
        self.user.google.delete()
        response = self.client.get(reverse("labels"))
        self.assertContains(response, "Connect Google to check")
        self.list_labels.assert_not_called()


class LabelsPerRunTests(TestCase):
    def test_cli_prompt_lines_are_unchanged(self):
        with run_context.use(run_context.RunContext(None)):
            self.assertEqual(
                labels.describe("🔐 Authorisations"),
                "🔐 Authorisations: Things you act on to get or grant access: sign-in codes, 2FA codes, "
                "password resets, account verification, invitations to join an account, team or project. "
                "Not warnings about your account (Alerts). Sub-labels: never.",
            )

    def test_runs_for_two_users_dont_share_labels(self):
        first, second = make_connected_user("a@example.com"), make_connected_user("b@example.com")
        first.labels.all().delete()
        Label.objects.create(user=first, name="Travel", sublabels="one per trip", include="Flights.", exclude="Not events.")
        with mock.patch.object(runner.google_connect, "credentials", return_value=None):
            first, second = runner.context(first), runner.context(second)
        with run_context.use(first):
            self.assertEqual(labels.describe("Travel"), "Travel: Flights. Not events. Sub-labels: one per trip.")
            self.assertFalse(labels.is_described("University"))
        with run_context.use(second):  # no labels yet: starts from labels.md
            self.assertTrue(labels.describe("Travel").startswith("Travel: Flights, trains, hotels"))


NOW = datetime.now(timezone.utc)
TODAY = NOW.date()
CLEANUP_MD = """## Travel
Sub-labels: one per trip
Cleanup: after the trip is over
Flights and hotels.

## Medical
Sub-labels: one per doctor
Cleanup: never
Doctors.
"""


def judgment(decision="delete", ended_days_ago=None, recheck_after=None):
    ended_on = TODAY - timedelta(days=ended_days_ago) if ended_days_ago is not None else None
    return sublabel_cleanup.Judgment(
        decision=decision, reason="Trip is over.", ended_on=ended_on, recheck_after=recheck_after
    )


class FakeGmail:
    """Labels as {id: (name, threads, [days ago of each email, newest first])}."""

    def __init__(self, labels):
        self.labels = labels
        self.deleted = []
        self.patcher = mock.patch.multiple(
            sublabel_cleanup.gmail,
            list_labels=lambda: [{"id": i, "name": name} for i, (name, _, _) in self.labels.items()],
            get_label=lambda i: {"id": i, "threadsTotal": self.labels[i][1]},
            # A message id is its label and age, so a new email gets a new id.
            list_label_ids=lambda i, limit=500: [f"{i}:{days}" for days in self.labels[i][2]][:limit],
            get_summary=self.summary,
            delete_label=self.deleted.append,
        )

    def summary(self, message_id):
        days_ago = int(message_id.split(":")[1])
        return {"id": message_id, "date": NOW - timedelta(days=days_ago), "sender": "s", "subject": "x", "snippet": ""}

    def __enter__(self):
        self.patcher.start()
        return self

    def __exit__(self, *exc):
        self.patcher.stop()


class SublabelCleanupTests(TestCase):
    def setUp(self):
        token = run_context._current.set(run_context.RunContext(None, labels=labels.parse(CLEANUP_MD)))
        self.addCleanup(run_context._current.reset, token)

    def test_never_deletes_anything_but_a_sub_label(self):
        with FakeGmail({}) as fake:
            for name in ("Travel", "Travel/Rome/Hotel"):
                with self.assertRaises(ValueError):
                    sublabel_cleanup.delete_sublabel("L1", name)
            self.assertEqual(fake.deleted, [])

    def test_only_sub_labels_under_described_parents_are_eligible(self):
        gmail_labels = [{"id": str(i), "name": name} for i, name in enumerate(
            ["Travel", "Travel/Rome", "Travel/Rome/Hotel", "Medical/Dr Weber", "Work/Launch"]
        )]
        self.assertEqual([l["name"] for l in sublabel_cleanup.eligible(gmail_labels)], ["Travel/Rome"])

    def test_candidates(self):
        def sub(threads, *days_ago):
            return sublabel_cleanup.Sublabel(
                "L1", "Travel/Rome", "Travel", threads, [],
                NOW - timedelta(days=days_ago[0]) if days_ago else None,
            )
        self.assertFalse(sublabel_cleanup.is_candidate(sub(1, 10), NOW))  # grace period
        self.assertTrue(sublabel_cleanup.is_candidate(sub(1, 20), NOW))
        self.assertFalse(sublabel_cleanup.is_candidate(sub(3, 30), NOW))
        self.assertTrue(sublabel_cleanup.is_candidate(sub(3, 50), NOW))
        self.assertTrue(sublabel_cleanup.is_candidate(sub(0), NOW))  # empty

    def test_grace_period_after_it_ended_beats_the_model(self):
        self.assertEqual(
            sublabel_cleanup.settle(judgment("delete", ended_days_ago=5), TODAY),
            ("keep", TODAY + timedelta(days=9)),
        )
        self.assertEqual(sublabel_cleanup.settle(judgment("delete", ended_days_ago=20), TODAY), ("delete", None))
        self.assertEqual(sublabel_cleanup.settle(judgment("keep"), TODAY), ("keep", TODAY + timedelta(days=30)))
        early = judgment("keep", ended_days_ago=10, recheck_after=TODAY + timedelta(days=1))
        self.assertEqual(sublabel_cleanup.settle(early, TODAY), ("keep", TODAY + timedelta(days=4)))

    def test_cleanup_hints_stay_out_of_the_classifier_prompt(self):
        self.assertEqual(labels.cleanup_hint("✈️ Travel"), "after the trip is over")
        self.assertNotIn("after the trip", labels.describe("Travel"))

    def test_dry_run_reports_and_apply_deletes(self):
        gmail_labels = {
            "T": ("Travel", 9, [1]),
            "R": ("Travel/Rome", 3, [60, 70]),      # candidate
            "L": ("Travel/Lisbon", 3, [5, 60]),     # recent email: never sent to the model
            "W": ("Medical/Dr Weber", 1, [90]),     # Cleanup: never
        }
        with FakeGmail(gmail_labels) as fake, mock.patch.object(
            sublabel_cleanup, "judge", return_value=judgment(ended_days_ago=60)
        ) as judge:
            dry = sublabel_cleanup.run(apply=False)
            self.assertEqual([(r.label_name, r.decision) for r in dry], [("Travel/Rome", "would_delete")])
            self.assertEqual(fake.deleted, [])
            applied = sublabel_cleanup.run(apply=True)
            self.assertEqual([(r.label_name, r.decision) for r in applied], [("Travel/Rome", "delete")])
            self.assertEqual(fake.deleted, ["R"])
            self.assertEqual([call.args[0].name for call in judge.call_args_list], ["Travel/Rome", "Travel/Rome"])

    def test_new_email_during_review_cancels_the_delete(self):
        fake = FakeGmail({"R": ("Travel/Rome", 3, [60])})

        def judge_while_an_email_arrives(sub, today):
            fake.labels["R"] = ("Travel/Rome", 4, [0, 60])
            return judgment(ended_days_ago=60)

        with fake, mock.patch.object(sublabel_cleanup, "judge", judge_while_an_email_arrives):
            reviews = sublabel_cleanup.run(apply=True)
        self.assertEqual(reviews[0].decision, "keep")
        self.assertEqual(fake.deleted, [])


@mock.patch.object(runner.google_connect, "credentials", return_value=None)
class CleanupRunnerTests(TestCase):
    def setUp(self):
        self.user = make_connected_user()
        Label.objects.create(user=self.user, name="Travel", sublabels="one per trip", cleanup="after the trip is over")
        self.fake = FakeGmail({"R": ("Travel/Rome", 3, [60]), "P": ("Travel/Paris", 3, [50])})
        self.fake.__enter__()
        self.addCleanup(self.fake.__exit__)

    def test_saves_reviews_and_skips_kept_ones_until_their_recheck(self, _):
        decisions = {"Travel/Rome": judgment("delete", ended_days_ago=60), "Travel/Paris": judgment("keep")}
        with mock.patch.object(sublabel_cleanup, "judge", lambda sub, today: decisions[sub.name]):
            cleanup_runner.run_user(self.user)
        self.user.google.refresh_from_db()
        self.assertEqual(self.user.google.last_cleanup_error, "")
        self.assertEqual(
            sorted(self.user.sublabel_reviews.values_list("label_name", "decision")),
            [("Travel/Paris", "keep"), ("Travel/Rome", "would_delete")],  # cleanup_enabled is off
        )
        self.assertEqual(cleanup_runner.last_result(self.user), {"removed": 0, "would_remove": 1})

        self.user.cleanup_enabled = True
        judged = []
        with mock.patch.object(sublabel_cleanup, "judge", lambda sub, today: judged.append(sub.name) or decisions[sub.name]):
            cleanup_runner.run_user(self.user)
        self.assertEqual(judged, ["Travel/Rome"])  # Paris waits for its recheck date
        self.assertEqual(self.fake.deleted, ["R"])
        self.assertEqual(cleanup_runner.last_result(self.user), {"removed": 1, "would_remove": 0})

    def test_due_monthly_after_connecting_or_the_last_cleanup(self, _):
        connection = self.user.google
        self.assertFalse(cleanup_runner.is_due(self.user))
        connection.connected_at = NOW - timedelta(days=31)
        self.assertTrue(cleanup_runner.is_due(self.user))
        connection.last_cleanup_at = NOW - timedelta(days=3)
        self.assertFalse(cleanup_runner.is_due(self.user))

    def test_button_requests_a_cleanup_and_dashboard_shows_the_count(self, _):
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("dashboard")), "Not yet")
        self.client.post(reverse("cleanup_now"))
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.cleanup_requested_at)

        connection = self.user.google
        connection.last_cleanup_at = NOW
        connection.save()
        for name in ("Travel/Rome", "Travel/Paris"):
            SublabelReview.objects.create(
                user=self.user, label_id=name, label_name=name, decision="delete", reviewed_at=NOW
            )
        self.assertContains(self.client.get(reverse("dashboard")), "2 sub-labels removed")

    def test_button_needs_a_connection(self, _):
        user = User.objects.create_user("b@example.com", "a-long-password")
        self.client.force_login(user)
        self.client.post(reverse("cleanup_now"))
        user.refresh_from_db()
        self.assertIsNone(user.cleanup_requested_at)

class GcalAvailabilityTests(TestCase):
    tz = ZoneInfo("UTC")
    day = date(2030, 1, 7)  # a Monday

    def _gaps(self, hours):
        now = datetime.combine(self.day, time.min, self.tz)
        return gcal._gaps(self.day, hours, self.tz, now, [], timedelta(minutes=30))

    def test_working_hours_limit_the_slot(self):
        (start, end), = self._gaps((time(9, 0), time(18, 0)))
        self.assertEqual((start.hour, end.hour), (9, 18))

    def test_anytime_covers_the_whole_day(self):
        (start, end), = self._gaps(None)
        self.assertEqual(end - start, timedelta(days=1))

    def test_free_slots_only_on_available_days(self):
        ctx = gcal.run_context.RunContext(None, work_days=(0,), anytime=True)
        now = datetime.combine(self.day, time.min, self.tz)
        with gcal.run_context.use(ctx), \
                mock.patch.object(gcal, "_timezone", return_value=self.tz), \
                mock.patch.object(gcal, "_window", return_value=(now, now + timedelta(days=3))), \
                mock.patch.object(gcal, "_busy", return_value=[]):
            slots = gcal.free_slots(3)
        self.assertEqual([s[0].date() for s in slots], [self.day])

