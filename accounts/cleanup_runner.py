"""Runs sub-label cleanup for one web user. Separate from runner.run_user: a failure
here never touches the email agent's status, and the other way round."""

from datetime import timedelta

from django.utils import timezone
from google.auth.exceptions import RefreshError

import run_context
import sublabel_cleanup

from . import runner
from .models import GoogleConnection, SublabelReview, User

INTERVAL = timedelta(days=30)


def is_due(user: User) -> bool:
    """Monthly: 30 days after the last cleanup, or after connecting if none yet."""
    connection = user.google
    return timezone.now() - (connection.last_cleanup_at or connection.connected_at) >= INTERVAL


def run_user(user: User) -> None:
    connection = user.google
    started = timezone.now()
    try:
        with run_context.use(runner.context(user)):
            reviews = sublabel_cleanup.run(user.cleanup_enabled, _waiting(user, started.date()))
        SublabelReview.objects.bulk_create(
            SublabelReview(user=user, reviewed_at=started, **vars(review)) for review in reviews
        )
        connection.last_cleanup_error = ""
    except RefreshError:
        connection.status = GoogleConnection.Status.RECONNECT_NEEDED
        connection.last_cleanup_error = "Google access expired or was revoked. Reconnect Google."
    except Exception as e:
        connection.last_cleanup_error = str(e)
    # Set even after a failure, so a broken run waits a month (or the button) instead of retrying every poll.
    connection.last_cleanup_at = started
    connection.save(update_fields=["status", "last_cleanup_error", "last_cleanup_at"])


def last_result(user: User) -> dict | None:
    """{"removed", "would_remove"}: how many sub-labels the last cleanup deleted, and how
    many it would have in a dry run. None if it never ran."""
    connection = user.google
    if not connection.last_cleanup_at:
        return None
    last_run = user.sublabel_reviews.filter(reviewed_at=connection.last_cleanup_at)
    return {
        "removed": last_run.filter(decision=SublabelReview.Decision.DELETE).count(),
        "would_remove": last_run.filter(decision=SublabelReview.Decision.WOULD_DELETE).count(),
    }


def _waiting(user: User, today):
    """Whether a sub-label was kept with a recheck date still ahead. Only its latest
    review counts."""
    latest = {}
    for label_id, recheck_after in user.sublabel_reviews.order_by("reviewed_at").values_list(
        "label_id", "recheck_after"
    ):
        latest[label_id] = recheck_after
    return lambda label_id: bool(latest.get(label_id) and latest[label_id] > today)
