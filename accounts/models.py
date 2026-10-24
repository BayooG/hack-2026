from datetime import time

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone


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

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()


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
