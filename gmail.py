"""Thin wrapper around the Gmail API."""

import base64
from functools import cache
from html.parser import HTMLParser

import google_auth

MAX_BODY_CHARS = 8000


@cache
def _service():
    return google_auth.service("gmail", "v1")


@cache
def _account_email() -> str:
    return _service().users().getProfile(userId="me").execute()["emailAddress"]


def message_url(message_id: str) -> str:
    """Link that opens the message in Gmail, in the right account."""
    return f"https://mail.google.com/mail/?authuser={_account_email()}#all/{message_id}"


def list_labels() -> list[dict]:
    """User-created labels only (no INBOX, SPAM, CATEGORY_*, ...)."""
    labels = _service().users().labels().list(userId="me").execute()["labels"]
    return [label for label in labels if label["type"] == "user"]


def add_label(message_id: str, label_id: str) -> None:
    _service().users().messages().modify(
        userId="me", id=message_id, body={"addLabelIds": [label_id]}
    ).execute()


def list_newest(n: int) -> list[dict]:
    """The newest n inbox messages as {id, sender, to, subject, body}."""
    messages = _service().users().messages()
    refs = messages.list(userId="me", labelIds=["INBOX"], maxResults=n).execute()
    return [
        _to_email(messages.get(userId="me", id=ref["id"], format="full").execute())
        for ref in refs.get("messages", [])
    ]


def _to_email(msg: dict) -> dict:
    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
    body = _find_body(msg["payload"], "text/plain")
    if not body:
        body = _strip_html(_find_body(msg["payload"], "text/html"))
    return {
        "id": msg["id"],
        "sender": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", ""),
        "body": (body.strip() or msg.get("snippet", ""))[:MAX_BODY_CHARS],
    }


def _find_body(part: dict, mime_type: str) -> str:
    data = part.get("body", {}).get("data")
    if part.get("mimeType") == mime_type and data:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    for sub in part.get("parts", []):
        if found := _find_body(sub, mime_type):
            return found
    return ""


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.chunks = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.chunks.append(data.strip())


def _strip_html(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return "\n".join(parser.chunks)
