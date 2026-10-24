from datetime import time
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from google.auth.exceptions import RefreshError

from . import google_connect, runner
from .models import GoogleConnection, ProcessedEmail, User


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
        data = {"email": "New@Example.com", "password1": "a-long-password", "password2": "a-long-password"}
        response = self.client.post(reverse("signup"), data)
        self.assertRedirects(response, reverse("dashboard"))
        self.assertTrue(User.objects.filter(email="new@example.com").exists())

        self.client.logout()
        response = self.client.post(reverse("signup"), {**data, "email": "NEW@example.com"})
        self.assertContains(response, "already exists")

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
