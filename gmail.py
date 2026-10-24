"""Thin wrapper around the Gmail API."""

import base64
import logging
from datetime import datetime, timezone
from email.message import EmailMessage
from html.parser import HTMLParser

import run_context

MAX_BODY_CHARS = 8000
# Retries for rate-limit (429, 403 rateLimitExceeded) and server errors. The client
# backs off exponentially with jitter; 8 retries wait about 4 minutes in total, enough
# to get past Gmail's per-minute quota window.
RETRIES = 8

# Show the client's "Sleeping N seconds before retry" warnings, which it hides by default.
_retry_handler = logging.StreamHandler()
_retry_handler.setFormatter(logging.Formatter("[gmail] %(message)s"))
logging.getLogger("googleapiclient.http").addHandler(_retry_handler)


def _service():
    return run_context.current().service("gmail", "v1")


def my_address() -> str:
    """The Gmail address of the account in the current run context."""
    return run_context.current().cached(
        "gmail:address",
        lambda: _service().users().getProfile(userId="me").execute(num_retries=RETRIES)["emailAddress"],
    )


def message_url(message_id: str) -> str:
    """Link that opens the message in Gmail, in the right account."""
    return f"https://mail.google.com/mail/?authuser={my_address()}#all/{message_id}"


def list_labels() -> list[dict]:
    """User-created labels only (no INBOX, SPAM, CATEGORY_*, ...)."""
    labels = _service().users().labels().list(userId="me").execute(num_retries=RETRIES)["labels"]
    return [label for label in labels if label["type"] == "user"]


def add_labels(message_id: str, label_ids: list[str]) -> None:
    _service().users().messages().modify(
        userId="me", id=message_id, body={"addLabelIds": label_ids}
    ).execute(num_retries=RETRIES)


def get_label_ids(message_id: str) -> list[str]:
    msg = _service().users().messages().get(
        userId="me", id=message_id, format="minimal"
    ).execute(num_retries=RETRIES)
    return msg.get("labelIds", [])


def create_label(name: str, color: dict | None = None) -> str:
    """Create a user label and return its id. "Parent/Child" nests it under Parent.
    color is {"textColor", "backgroundColor"}, as list_labels returns it."""
    body = {"name": name} | ({"color": color} if color else {})
    label = _service().users().labels().create(userId="me", body=body).execute(num_retries=RETRIES)
    return label["id"]


def set_label_color(label_id: str, color: dict) -> None:
    _service().users().labels().patch(userId="me", id=label_id, body={"color": color}).execute(num_retries=RETRIES)


def get_label(label_id: str) -> dict:
    """One label with its counts (threadsTotal, messagesTotal, ...)."""
    return _service().users().labels().get(userId="me", id=label_id).execute(num_retries=RETRIES)


def delete_label(label_id: str) -> None:
    """Delete a label. Its messages stay, just without this label."""
    _service().users().labels().delete(userId="me", id=label_id).execute(num_retries=RETRIES)


def list_label_ids(label_id: str, limit: int = 500) -> list[str]:
    """Ids of the messages under a label, newest first."""
    refs = _service().users().messages().list(
        userId="me", labelIds=[label_id], maxResults=limit
    ).execute(num_retries=RETRIES)
    return [ref["id"] for ref in refs.get("messages", [])]


def get_summary(message_id: str) -> dict:
    """One message as {id, date, sender, subject, snippet}, without its body."""
    msg = _service().users().messages().get(
        userId="me", id=message_id, format="metadata", metadataHeaders=["From", "Subject"]
    ).execute(num_retries=RETRIES)
    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
    return {
        "id": msg["id"],
        "date": datetime.fromtimestamp(int(msg["internalDate"]) / 1000, timezone.utc),
        "sender": headers.get("from", ""),
        "subject": headers.get("subject", ""),
        "snippet": msg.get("snippet", ""),
    }


def list_inbox_ids(limit: int, after: datetime | None = None) -> list[str]:
    """Ids of the newest inbox messages (newest first), optionally only those
    received after a moment."""
    query = f"after:{int(after.timestamp())}" if after else ""
    refs = _service().users().messages().list(
        userId="me", labelIds=["INBOX"], q=query, maxResults=limit
    ).execute(num_retries=RETRIES)
    return [ref["id"] for ref in refs.get("messages", [])]


def get_email(message_id: str) -> dict:
    """One message as {id, thread_id, sender, to, cc, subject, body}."""
    msg = _service().users().messages().get(
        userId="me", id=message_id, format="full"
    ).execute(num_retries=RETRIES)
    return _to_email(msg)


def list_newest(n: int) -> list[dict]:
    """The newest n inbox messages as {id, thread_id, sender, to, cc, subject, body}."""
    return [get_email(message_id) for message_id in list_inbox_ids(n)]


def thread_has_draft(thread_id: str) -> bool:
    drafts = _service().users().drafts().list(userId="me", maxResults=500).execute(num_retries=RETRIES)
    return any(d["message"]["threadId"] == thread_id for d in drafts.get("drafts", []))


def get_reply_headers(message_id: str) -> dict:
    """Lower-cased headers needed to reply to a message, plus its thread_id."""
    msg = _service().users().messages().get(
        userId="me", id=message_id, format="metadata",
        metadataHeaders=["From", "Reply-To", "To", "Cc", "Subject", "Message-ID", "References", "List-Id"],
    ).execute(num_retries=RETRIES)
    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
    return headers | {"thread_id": msg["threadId"]}


def create_reply_draft(original: dict, to: str, cc: list[str], body: str) -> str:
    """Save body as a draft reply in the original's thread. original comes from
    get_reply_headers. Returns the draft id."""
    reply = EmailMessage()
    reply["To"] = to
    if cc:
        reply["Cc"] = ", ".join(cc)
    subject = original.get("subject", "")
    reply["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    # These two headers make other mail clients thread the reply too.
    if message_id_header := original.get("message-id"):
        reply["In-Reply-To"] = message_id_header
        reply["References"] = " ".join(filter(None, [original.get("references"), message_id_header]))
    reply.set_content(body)

    raw = base64.urlsafe_b64encode(reply.as_bytes()).decode()
    draft = _service().users().drafts().create(
        userId="me", body={"message": {"raw": raw, "threadId": original["thread_id"]}}
    ).execute(num_retries=RETRIES)
    return draft["id"]


def _to_email(msg: dict) -> dict:
    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
    body = _find_body(msg["payload"], "text/plain")
    if not body:
        body = _strip_html(_find_body(msg["payload"], "text/html"))
    return {
        "id": msg["id"],
        "thread_id": msg["threadId"],
        "sender": headers.get("from", ""),
        "to": headers.get("to", ""),
        "cc": headers.get("cc", ""),
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
