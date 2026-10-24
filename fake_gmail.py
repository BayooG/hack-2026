"""Stand-in for the Gmail API. Same response shapes, no network.

Swap these functions for real Gmail calls later.
"""


def list_labels() -> list[dict]:
    """Mirrors users.labels.list -> response["labels"]."""
    return [
        {"id": "Label_1", "name": "Documents", "type": "user"},
        {"id": "Label_2", "name": "Medical", "type": "user"},
        {"id": "Label_3", "name": "Newsletters", "type": "user"},
        {"id": "Label_4", "name": "PhD", "type": "user"},
        {"id": "Label_5", "name": "University", "type": "user"},
    ]


def add_label(message_id: str, label_id: str) -> None:
    """Mirrors users.messages.modify with addLabelIds."""
    print(f"[gmail] {message_id}: add label {label_id}")
