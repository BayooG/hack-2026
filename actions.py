"""Actions the agent can take after classifying an email.

Each one is a plain function: the code calls it directly for the required
minimum, and Claude can call it as a tool for anything beyond that.
apply_label changes the real Gmail inbox; the others only print until
their real behaviour is specced.
"""

from anthropic import beta_tool

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


def block_time(title: str, minutes: int) -> str:
    """Block the next free slot in the user's calendar to respond to an email.

    Args:
        title: Calendar event title.
        minutes: Length of the block in minutes.
    """
    print(f"[action] block {minutes} min in calendar: {title}")
    return f"blocked: today 14:00 for {minutes} min"


def draft_reply(to: str, body: str) -> str:
    """Save a reply as a draft for the user to review. Never sends it.

    Args:
        to: Recipient email address.
        body: Text of the reply.
    """
    print(f"[action] draft reply to {to}:\n{body}")
    return "draft saved"


ALL = [apply_label, notify_now, add_to_digest, block_time, draft_reply]
TOOLS = [beta_tool(fn) for fn in ALL]
