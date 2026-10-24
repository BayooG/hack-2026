"""Actions the agent can take after classifying an email.

Each one is a plain function: the code calls it directly for the required
minimum, and Claude can call it as a tool for anything beyond that.
apply_label and block_time change the real Gmail inbox and calendar;
notify_now, add_to_digest and draft_reply only print until specced.
"""

from datetime import timedelta

from anthropic import beta_tool

import gcal
import gmail


def apply_label(message_id: str, label_name: str) -> str:
    """Apply a Gmail label to the email.

    Args:
        message_id: Gmail id of the email.
        label_name: Name of an existing Gmail label.
    """
    label_ids = {label["name"]: label["id"] for label in gmail.list_labels()}
    if label_name not in label_ids:
        return f"error: no label named {label_name}"
    gmail.add_label(message_id, label_ids[label_name])
    return f"labelled {label_name}"


def notify_now(summary: str) -> str:
    """Alert the user immediately. Only for emails that cannot wait.

    Args:
        summary: One sentence telling the user what needs attention.
    """
    print(f"[action] notify now: {summary}")
    return "notified"


def add_to_digest(summary: str) -> str:
    """Add the email to the user's daily summary.

    Args:
        summary: One sentence describing the email.
    """
    print(f"[action] add to digest: {summary}")
    return "added to digest"


def block_time(message_id: str, title: str, minutes: int, notes: str) -> str:
    """Block the next free slot in the user's calendar to respond to an email.

    Args:
        message_id: Gmail id of the email the time is for; the event links to it.
        title: Calendar event title.
        minutes: Length of the block in minutes.
        notes: Event description: who the email is from, what it asks, any
            deadline, and what to prepare.
    """
    if existing := gcal.find_event_for_message(message_id):
        if existing["status"] == "cancelled":
            return "not blocked: the user deleted the earlier block for this email"
        return f"already blocked {gcal.format_slot(gcal.event_slot(existing))}"
    slots = gcal.free_slots(min_minutes=minutes)
    if not slots:
        return "error: no free slot in the next days"
    start, _ = slots[0]
    slot = (start, start + timedelta(minutes=minutes))
    url = gmail.message_url(message_id)
    link = gcal.create_event(title, slot, f"{notes}\n\nEmail: {url}", message_id, url)
    print(f"[action] blocked {gcal.format_slot(slot)} in calendar: {title} ({link})")
    return f"blocked {gcal.format_slot(slot)}"


def find_free_slots(days: int, min_minutes: int) -> str:
    """List the user's free calendar slots during working hours, for example to propose meeting times.

    Args:
        days: How many days ahead to look, starting today.
        min_minutes: Shortest slot worth listing, in minutes.
    """
    slots = gcal.free_slots(days, min_minutes)
    return "\n".join(gcal.format_slot(s) for s in slots) or "no free slots"


def draft_reply(to: str, body: str) -> str:
    """Save a reply as a draft for the user to review. Never sends it.

    Args:
        to: Recipient email address.
        body: Text of the reply.
    """
    print(f"[action] draft reply to {to}:\n{body}")
    return "draft saved"


ALL = [apply_label, notify_now, add_to_digest, block_time, find_free_slots, draft_reply]
TOOLS = [beta_tool(fn) for fn in ALL]
