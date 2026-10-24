"""Sub-label cleanup: deletes sub-labels whose trip, event, application or course is
over. Separate from the email agent: code picks the candidates and enforces the safety
rules, and Claude decides keep or delete for each candidate.

Only sub-labels one level deep (Parent/Child) under a described label are ever looked
at. Top-level labels are never deleted. Deleting a sub-label leaves its emails under
the parent, which apply_label always applies too.

Usage:
    python sublabel_cleanup.py           # dry run for the token.json account
    python sublabel_cleanup.py --apply   # delete for real
"""

import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Literal

import anthropic
from google.auth.exceptions import RefreshError
from pydantic import BaseModel

import gmail
import labels

MODEL = "claude-sonnet-5-5"
GRACE_DAYS = 14         # never review a sub-label with an email this recent, nor delete one this soon after it ended
SINGLE_QUIET_DAYS = 14  # one conversation and no new email for this long: a candidate
QUIET_DAYS = 45         # no new email for this long: a candidate
DEFAULT_RECHECK_DAYS = 30
MAX_RECHECK_DAYS = 180
RECENT_EMAILS = 5       # emails shown to Claude per candidate

Decision = Literal["keep", "delete", "would_delete", "error"]

SYSTEM_PROMPT = f"""You decide whether a Gmail sub-label is done and can be deleted. Deleting it removes only the sub-label: its emails stay under the parent label.

Inputs: today's date, the parent label (its description and its Cleanup hint), the sub-label's name, how many conversations it has, the dates of its first and newest email, and its newest emails (date, sender, subject, snippet).

decision (keep or delete)
- delete: the thing the sub-label is about (a trip, event, exam, course, application, programme) ended at least {GRACE_DAYS} days ago, or the sub-label holds a one-off that will not get another email.
- keep: something is still ahead (a future trip, event, exam, interview or decision mentioned in the emails), or it ended less than {GRACE_DAYS} days ago.
- When unsure, keep.
- Follow the parent's Cleanup hint.

reason
One sentence on why, naming the date or fact it rests on.

ended_on (date or null)
When the thing the sub-label is about ended or will end, e.g. the last day of the trip, the event date, the exam date, the day of a rejection. Null when there is no such date, e.g. a one-off email.

recheck_after (date or null)
For keep: when to look again, at least {GRACE_DAYS} days after ended_on. Null for delete, or when there is no better date than in a month."""


class Judgment(BaseModel):
    decision: Literal["keep", "delete"]
    reason: str
    ended_on: date | None
    recheck_after: date | None


@dataclass
class Sublabel:
    id: str
    name: str      # "Parent/Child"
    parent: str
    threads: int
    message_ids: list[str]  # newest first
    last_email_at: datetime | None


@dataclass
class Review:
    label_id: str
    label_name: str
    threads: int
    last_email_at: datetime | None
    decision: Decision
    reason: str
    recheck_after: date | None = None


def eligible(gmail_labels: list[dict]) -> list[dict]:
    """The sub-labels cleanup may look at: one level deep, under a described parent
    whose Cleanup line isn't never. Top-level labels never pass."""
    found = []
    for label in gmail_labels:
        if label["name"].count("/") != 1:
            continue
        parent = label["name"].split("/", 1)[0]
        if labels.is_described(parent) and not labels.cleanup_never(parent):
            found.append(label)
    return found


def measure(label: dict) -> Sublabel:
    ids = gmail.list_label_ids(label["id"])
    return Sublabel(
        id=label["id"],
        name=label["name"],
        parent=label["name"].split("/", 1)[0],
        threads=gmail.get_label(label["id"]).get("threadsTotal", 0),
        message_ids=ids,
        last_email_at=gmail.get_summary(ids[0])["date"] if ids else None,
    )


def is_candidate(sub: Sublabel, now: datetime) -> bool:
    if sub.last_email_at is None:
        return True  # empty
    quiet = now - sub.last_email_at
    if quiet < timedelta(days=GRACE_DAYS):
        return False
    return (sub.threads <= 1 and quiet >= timedelta(days=SINGLE_QUIET_DAYS)) or quiet >= timedelta(days=QUIET_DAYS)


def judge(sub: Sublabel, today: date) -> Judgment:
    recent = [gmail.get_summary(message_id) for message_id in sub.message_ids[:RECENT_EMAILS]]
    first = gmail.get_summary(sub.message_ids[-1]) if len(sub.message_ids) > RECENT_EMAILS else recent[-1]
    facts = {
        "today": today.isoformat(),
        "parent_label": labels.describe(sub.parent),
        "cleanup_hint": labels.cleanup_hint(sub.parent) or "none",
        "sublabel": sub.name.split("/", 1)[1],
        "conversations": sub.threads,
        "first_email": first["date"].date().isoformat(),
        "newest_email": recent[0]["date"].date().isoformat(),
        "newest_emails": [
            {"date": e["date"].date().isoformat(), "from": e["sender"], "subject": e["subject"], "snippet": e["snippet"]}
            for e in recent
        ],
    }
    response = anthropic.Anthropic().messages.parse(
        model=MODEL,
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(facts, indent=2, ensure_ascii=False)}],
        output_format=Judgment,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"Model refused: {response.stop_details}")
    return response.parsed_output


def settle(judgment: Judgment, today: date) -> tuple[Literal["keep", "delete"], date | None]:
    """The decision and recheck date after the grace period and recheck limits,
    whatever the model said."""
    grace_ends = judgment.ended_on + timedelta(days=GRACE_DAYS) if judgment.ended_on else None
    if judgment.decision == "delete":
        if grace_ends and today < grace_ends:
            return "keep", grace_ends
        return "delete", None
    recheck = judgment.recheck_after
    if recheck is None or recheck <= today or recheck > today + timedelta(days=MAX_RECHECK_DAYS):
        recheck = today + timedelta(days=DEFAULT_RECHECK_DAYS)
    if grace_ends and recheck < grace_ends:
        recheck = grace_ends
    return "keep", recheck


def delete_sublabel(label_id: str, label_name: str) -> None:
    """Delete a sub-label. Refuses anything not exactly one level deep, so a top-level
    label can never be deleted, whatever called this."""
    if label_name.count("/") != 1:
        raise ValueError(f"refusing to delete {label_name}: not a sub-label")
    gmail.delete_label(label_id)


def review(sub: Sublabel, today: date, apply: bool) -> Review:
    def done(decision: Decision, reason: str, recheck: date | None = None) -> Review:
        return Review(sub.id, sub.name, sub.threads, sub.last_email_at, decision, reason, recheck)

    if sub.last_email_at is None:
        judgment = Judgment(decision="delete", reason="No emails left under it.", ended_on=None, recheck_after=None)
    else:
        judgment = judge(sub, today)
    decision, recheck = settle(judgment, today)
    reason = judgment.reason
    if decision == "keep":
        if judgment.decision == "delete":
            reason += f" Kept for the {GRACE_DAYS}-day grace period after it ended."
        return done("keep", reason, recheck)
    if not apply:
        return done("would_delete", reason)
    # A new email since the model was asked cancels the delete; the next run looks again.
    if gmail.list_label_ids(sub.id, limit=1) != sub.message_ids[:1]:
        return done("keep", "A new email arrived during the review.")
    delete_sublabel(sub.id, sub.name)
    return done("delete", reason)


def run(apply: bool, waiting: Callable[[str], bool] = lambda label_id: False) -> list[Review]:
    """Review every eligible sub-label that is a candidate and isn't waiting for its
    recheck date; delete the done ones if apply, else report them as would_delete."""
    now = datetime.now(timezone.utc)
    reviews = []
    for label in eligible(gmail.list_labels()):
        if waiting(label["id"]):
            continue
        try:
            sub = measure(label)
            if is_candidate(sub, now):
                reviews.append(review(sub, now.date(), apply))
        except (RefreshError, anthropic.AnthropicError):
            raise  # not this sub-label's fault: stop and retry the whole run later
        except Exception as e:
            reviews.append(Review(label["id"], label["name"], 0, None, "error", str(e)))
    return reviews


def main() -> None:
    apply = "--apply" in sys.argv[1:]
    for warning in labels.check([label["name"] for label in gmail.list_labels()]):
        print(f"[labels] {warning}")
    reviews = run(apply)
    for r in reviews:
        recheck = f" (recheck {r.recheck_after})" if r.recheck_after else ""
        print(f"{r.decision:>12}  {r.label_name}: {r.reason}{recheck}")
    removed = sum(r.decision in ("delete", "would_delete") for r in reviews)
    print(f"\n{'Removed' if apply else 'Would remove (dry run)'}: {removed} sub-label(s)")


if __name__ == "__main__":
    main()
