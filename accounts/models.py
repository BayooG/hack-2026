from datetime import time

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone

import labels


class UserManager(BaseUserManager):
    use_in_migrations = True

    def get_by_natural_key(self, email):
        return self.get(email__iexact=email)

    def create_user(self, email, password=None, **extra_fields):
        user = self.model(email=email.strip().lower(), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        return self.create_user(email, password, is_staff=True, is_superuser=True, **extra_fields)


class User(AbstractUser):
    """Logs in with email; also holds the user's agent settings."""

    username = None
    email = models.EmailField(unique=True)
    agent_enabled = models.BooleanField(default=True)
    work_start = models.TimeField(default=time(9, 0))
    work_end = models.TimeField(default=time(18, 0))
    run_requested_at = models.DateTimeField(null=True, blank=True)
    # Off: sub-label cleanup only reports what it would delete.
    cleanup_enabled = models.BooleanField(default=False)
    cleanup_requested_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()


class Label(models.Model):
    """What one of the user's Gmail labels is for, as the classifier reads it."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="labels")
    position = models.PositiveIntegerField(default=0)
    emoji = models.CharField(max_length=16, blank=True)
    name = models.CharField(max_length=60, help_text="The Gmail label name, without the emoji")
    sublabels = models.CharField(
        max_length=80, blank=True, help_text='"never", or a rule like "one per trip". Blank means when useful'
    )
    include = models.TextField(max_length=500, blank=True, help_text="What belongs under the label")
    exclude = models.TextField(max_length=300, blank=True, help_text="What doesn't, and where it goes instead")
    # Set from labels.md's Cleanup: lines; read by sub-label cleanup, not shown in the editor yet.
    cleanup = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"{self.emoji} {self.name}".strip()

    def entry(self) -> dict:
        """The label in the shape labels.parse gives for a labels.md section."""
        return {
            "name": self.name, "emoji": self.emoji, "sublabels": self.sublabels,
            "include": self.include, "exclude": self.exclude, "cleanup": self.cleanup,
        }


def seed_labels(user: User) -> None:
    """Give the user the labels from labels.md."""
    entries = labels.parse(labels.LABELS_FILE.read_text()).values()
    Label.objects.bulk_create(
        Label(user=user, position=i, **{field: entry[field] for field in Label().entry()})
        for i, entry in enumerate(entries)
    )


def user_labels(user: User) -> list[Label]:
    """The user's labels; a user with none starts from labels.md."""
    if not user.labels.exists():
        seed_labels(user)
    return list(user.labels.all())


class GoogleConnection(models.Model):
    class Status(models.TextChoices):
        CONNECTED = "connected"
        RECONNECT_NEEDED = "reconnect_needed", "Reconnect needed"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="google")
    google_email = models.EmailField()
    refresh_token = models.TextField(help_text="Fernet-encrypted")
    status = models.CharField(max_length=20, choices=Status, default=Status.CONNECTED)
    # Only emails received after this are processed, so connecting never
    # works through an existing inbox.
    connected_at = models.DateTimeField(default=timezone.now)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    # When sub-label cleanup last ran; its reviews from that run share this time.
    last_cleanup_at = models.DateTimeField(null=True, blank=True)
    last_cleanup_error = models.TextField(blank=True)


class ProcessedEmail(models.Model):
    class Status(models.TextChoices):
        OK = "ok"
        ERROR = "error"

    class Urgency(models.TextChoices):
        NOT_URGENT = "not_urgent", "Not urgent"
        FOR_SUMMARY = "for_summary", "For summary"
        SCHEDULE_TIME = "schedule_time", "Schedule time"
        INSTANT = "instant", "Instant"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="processed_emails")
    message_id = models.CharField(max_length=64)
    processed_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=10, choices=Status, default=Status.OK)
    subject = models.CharField(max_length=255, blank=True)
    labels = models.CharField(max_length=255, blank=True, help_text="Applied labels, one per line")
    urgency = models.CharField(max_length=20, choices=Urgency, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-processed_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "message_id"], name="unique_processed_email"),
        ]

    @property
    def label_list(self) -> list[str]:
        return [label for label in self.labels.splitlines() if label]


class SublabelReview(models.Model):
    """One sub-label looked at by sub-label cleanup, kept or deleted."""

    class Decision(models.TextChoices):
        KEEP = "keep"
        DELETE = "delete"
        WOULD_DELETE = "would_delete", "Would delete"
        ERROR = "error"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sublabel_reviews")
    label_id = models.CharField(max_length=64)
    label_name = models.CharField(max_length=255, help_text="Parent/Child, kept after deletion")
    threads = models.PositiveIntegerField(default=0)
    last_email_at = models.DateTimeField(null=True, blank=True)
    decision = models.CharField(max_length=20, choices=Decision)
    reason = models.TextField(blank=True)
    recheck_after = models.DateField(null=True, blank=True)
    reviewed_at = models.DateTimeField(help_text="When the cleanup run started")

    class Meta:
        ordering = ["-reviewed_at", "label_name"]
