"""Thin wrapper around the Gmail API.

The first run opens a browser to authorize and saves token.json.
"""

import base64
import logging
import os
from email.message import EmailMessage
from functools import cache
from html.parser import HTMLParser

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
TOKEN_FILE = "token.json"
CREDENTIALS_FILE = "credentials.json"
MAX_BODY_CHARS = 8000
# Retries for rate-limit (429, 403 rateLimitExceeded) and server errors. The client
# backs off exponentially with jitter; 8 retries wait about 4 minutes in total, enough
# to get past Gmail's per-minute quota window.
RETRIES = 8

# Show the client's "Sleeping N seconds before retry" warnings, which it hides by default.
_retry_handler = logging.StreamHandler()
_retry_handler.setFormatter(logging.Formatter("[gmail] %(message)s"))
logging.getLogger("googleapiclient.http").addHandler(_retry_handler)


@cache
def _service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE)
        if not creds.has_scopes(SCOPES):
            creds = None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


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


def list_newest(n: int) -> list[dict]:
    """The newest n inbox messages as {id, thread_id, sender, to, cc, subject, body}."""
    messages = _service().users().messages()
    refs = messages.list(userId="me", labelIds=["INBOX"], maxResults=n).execute(num_retries=RETRIES)
    return [
        _to_email(messages.get(userId="me", id=ref["id"], format="full").execute(num_retries=RETRIES))
        for ref in refs.get("messages", [])
    ]


def thread_has_draft(thread_id: str) -> bool:
    drafts = _service().users().drafts().list(userId="me", maxResults=500).execute(num_retries=RETRIES)
    return any(d["message"]["threadId"] == thread_id for d in drafts.get("drafts", []))


@cache
def my_address() -> str:
    return _service().users().getProfile(userId="me").execute(num_retries=RETRIES)["emailAddress"]


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
