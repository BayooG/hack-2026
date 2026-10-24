"""Actions the agent can take after classifying an email.

Each one is a plain function: the code calls it directly for the required
minimum, and Claude can call the ones in ALL as tools for anything beyond that.
apply_label, draft_reply and block_time change the real Gmail inbox and calendar;
notify_now and add_to_digest only print until their real behaviour is specced.
"""

from email.utils import getaddresses

from anthropic import beta_tool

import gcal
import gmail
import labels


# Most of the user's labels one email can hold, sub-labels included.
MAX_LABELS = 3


def apply_label(message_id: str, label_name: str, sublabel: str | None = None) -> str:
    """Apply a Gmail label to the email, and optionally a sub-label under it.
    An email holds at most 3 labels, and each sub-label counts as one.

    Args:
        message_id: Gmail id of the email.
        label_name: Name of an existing top-level Gmail label.
        sublabel: Name of a sub-label under label_name, without the parent (e.g. "Berlin Hackathon").
            Created if it doesn't exist. One level deep only; never for labels whose sub-label rule in labels.md is never.
    """
    # Gmail label names are unique ignoring case, so match them that way.
    in_gmail = {label["name"].lower(): label for label in gmail.list_labels()}
    if "/" in label_name:
        return f"error: {label_name} is a sub-label; pass its parent as label_name and the rest as sublabel"
    if label_name.lower() not in in_gmail:
        return f"error: no label named {label_name}"
    parent = in_gmail[label_name.lower()]

    # Only the user's own labels count, not INBOX, UNREAD and the like.
    on_email = set(gmail.get_label_ids(message_id)) & {label["id"] for label in in_gmail.values()}
    room = MAX_LABELS - len(on_email | {parent["id"]})
    if room < 0:
        return f"error: the email already has {MAX_LABELS} labels; {parent['name']} not applied"

    # Sub-labels are one level deep, so a / in the name (like "AI/ML") can't nest it further.
    sublabel = (sublabel or "").strip().replace("/", "-")
    full_name = f"{parent['name']}/{sublabel}"
    existing = in_gmail.get(full_name.lower())
    # A refused sub-label still gets the parent, so the email is never left unlabelled.
    skipped = ""
    if not sublabel:
        pass
    elif not labels.allows_sublabel(parent["name"]):
        skipped = f" (sub-label skipped: {parent['name']} never gets sub-labels)"
    elif room == 0 and not (existing and existing["id"] in on_email):
        skipped = f" (sub-label skipped: the email already has {MAX_LABELS} labels)"
    if not sublabel or skipped:
        gmail.add_labels(message_id, [parent["id"]])
        return f"labelled {parent['name']}{skipped}"

    # Sub-labels take the parent's colour; an existing one is brought back in line if it differs.
    color = parent.get("color")
    if existing:
        sub_id, full_name, new = existing["id"], existing["name"], ""
        if color and existing.get("color") != color:
            gmail.set_label_color(sub_id, color)
    else:
        sub_id, new = gmail.create_label(full_name, color), " (new sub-label)"
        print(f"[action] created sub-label {full_name}")
    # The parent goes on too, so the email still shows under the parent label.
    gmail.add_labels(message_id, [parent["id"], sub_id])
    return f"labelled {parent['name']} and {full_name}{new}"


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
    """Block a free slot in the user's calendar to respond to an email, right
    next to existing email time when possible, else the next free slot.

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
    if not (picked := gcal.reply_slot(minutes)):
        return "error: no free slot in the next days"
    slot, anchor = picked
    url = gmail.message_url(message_id)
    link = gcal.create_event(title, slot, f"{notes}\n\nEmail: {url}", message_id, url)
    where = f"blocked {gcal.format_slot(slot)}"
    if anchor:
        where += f" (next to existing email time {gcal.format_slot(anchor)})"
    print(f"[action] {where} in calendar: {title} ({link})")
    return where


def find_free_slots(days: int, min_minutes: int) -> str:
    """List the user's free calendar slots during working hours, for example to propose meeting times.

    Args:
        days: How many days ahead to look, starting today.
        min_minutes: Shortest slot worth listing, in minutes.
    """
    slots = gcal.free_slots(days, min_minutes)
    return "\n".join(gcal.format_slot(s) for s in slots) or "no free slots"


MAX_REPLY_ALL_RECIPIENTS = 10


def draft_reply(message_id: str, body: str, reply_all: bool = False, reply_all_reason: str = "") -> str:
    """Save a reply to the email as a Gmail draft in its thread. Never sends it.
    Not a tool: only the write-reply step calls it, so every draft follows the skill.

    Reply all is dropped for mailing lists and for more than MAX_REPLY_ALL_RECIPIENTS
    recipients. A reply-all draft starts with a check note for the user to delete.
    """
    original = gmail.get_reply_headers(message_id)
    if gmail.thread_has_draft(original["thread_id"]):
        return "skipped: this thread already has a draft"

    to = original.get("reply-to") or original.get("from", "")
    cc, blocked = [], ""
    if reply_all:
        cc = _others_on_thread(original, to)
        if original.get("list-id"):
            cc, blocked = [], "mailing list"
        elif len(cc) + 1 > MAX_REPLY_ALL_RECIPIENTS:
            cc, blocked = [], f"{len(cc) + 1} recipients"
    if cc:
        body = (f"[REPLY-ALL CHECK: also sent to {', '.join(cc)}. Reason: {reply_all_reason.rstrip('.') or 'none given'}. "
                "Delete this line before sending.]\n\n" + body)

    gmail.create_reply_draft(original, to, cc, body)
    print(f"[action] draft reply to {to}" + (f", cc {', '.join(cc)}" if cc else "") + f":\n{body}")
    if blocked:
        return f"draft saved to sender only (reply all blocked: {blocked})"
    return "draft saved, reply all" if cc else "draft saved"


def _others_on_thread(original: dict, to: str) -> list[str]:
    """Addresses on the original's To and Cc, without the user, the reply's To and duplicates."""
    seen = {gmail.my_address().lower()} | {addr.lower() for _, addr in getaddresses([to])}
    others = []
    for _, addr in getaddresses([original.get("to", ""), original.get("cc", "")]):
        if addr and addr.lower() not in seen:
            seen.add(addr.lower())
            others.append(addr)
    return others


# draft_reply is left out on purpose: drafts only come from the write-reply skill.
ALL = [apply_label, notify_now, add_to_digest, block_time, find_free_slots]
TOOLS = [beta_tool(fn) for fn in ALL]
