"""Runs the email agent for one web user."""

import anthropic
from django.utils import timezone
from google.auth.exceptions import RefreshError

import agent
import gmail
import labels
import run_context

from . import google_connect
from .models import GoogleConnection, ProcessedEmail, User, user_labels

LOOKBACK = 100    # newest inbox ids to check for unprocessed emails
MAX_PER_RUN = 10  # emails handed to Claude per run


def context(user: User) -> run_context.RunContext:
    """The user's Google account, settings and labels, for running the agent as them."""
    return run_context.RunContext(
        google_connect.credentials(user.google),
        user.work_start,
        user.work_end,
        # Accounts made before signup asked for names have none: fall back to the email's local part.
        first_name=user.first_name or user.email.split("@")[0],
        last_name=user.last_name,
        labels={labels.key(label.name): label.entry() for label in user_labels(user)},
    )


def run_user(user: User) -> None:
    connection = user.google
    ctx = context(user)
    try:
        with run_context.use(ctx):
            _process_new_emails(user, connection)
        connection.last_error = ""
    except RefreshError:
        connection.status = GoogleConnection.Status.RECONNECT_NEEDED
        connection.last_error = "Google access expired or was revoked. Reconnect Google."
    except Exception as e:
        connection.last_error = str(e)
    connection.last_run_at = timezone.now()
    connection.save()


def _label_names() -> list[str]:
    return [label["name"] for label in gmail.list_labels()]


def _process_new_emails(user: User, connection: GoogleConnection) -> None:
    label_names = _label_names()
    if not [name for name in label_names if "/" not in name]:
        raise RuntimeError("No labels in Gmail. Create the labels the agent should choose from.")
    for warning in labels.check(label_names):
        print(f"[labels] {user.email}: {warning}")

    ids = gmail.list_inbox_ids(LOOKBACK, after=connection.connected_at)
    done = set(user.processed_emails.filter(message_id__in=ids).values_list("message_id", flat=True))
    new = [message_id for message_id in reversed(ids) if message_id not in done]  # oldest first

    for message_id in new[:MAX_PER_RUN]:
        record = ProcessedEmail(user=user, message_id=message_id)
        try:
            email = agent.Email(**gmail.get_email(message_id))
            record.subject = email.subject[:255]
            # Read per email, so a sub-label made for one email is reused by the next.
            result = agent.run(email, _label_names())
            record.labels = "\n".join(
                f"{label}/{sublabel}" if sublabel else label
                for label, sublabel in agent.pick_labels(result)
            )[:255]
            record.urgency = result.urgency
        except (RefreshError, anthropic.AnthropicError):
            raise  # not this email's fault: stop the run and retry it next time
        except Exception as e:
            record.status, record.error = ProcessedEmail.Status.ERROR, str(e)
        record.save()
