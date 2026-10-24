import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from accounts import cleanup_runner, runner
from accounts.models import GoogleConnection, User

INTERVAL = timedelta(minutes=5)
POLL_SECONDS = 10


class Command(BaseCommand):
    help = (
        "Run the agent for every enabled user every 5 minutes, and on 'Run now'. "
        "Run sub-label cleanup once a month, and on 'Clean up sub-labels'."
    )

    def handle(self, *args, **options):
        next_scheduled = timezone.now()
        while True:
            requested = Q(run_requested_at__isnull=False)
            if timezone.now() >= next_scheduled:
                requested |= Q(agent_enabled=True)
                next_scheduled = timezone.now() + INTERVAL

            users = User.objects.filter(
                requested, google__status=GoogleConnection.Status.CONNECTED
            ).select_related("google")
            for user in users:
                # Clear first, so a click during this run triggers another one.
                User.objects.filter(pk=user.pk).update(run_requested_at=None)
                self.stdout.write(f"{timezone.now():%H:%M:%S} running for {user.email}")
                runner.run_user(user)
            self.clean_up_sublabels()
            time.sleep(POLL_SECONDS)

    def clean_up_sublabels(self):
        users = User.objects.filter(google__status=GoogleConnection.Status.CONNECTED).filter(
            Q(cleanup_requested_at__isnull=False) | Q(agent_enabled=True)
        ).select_related("google")
        for user in users:
            if user.cleanup_requested_at is None and not cleanup_runner.is_due(user):
                continue
            User.objects.filter(pk=user.pk).update(cleanup_requested_at=None)
            self.stdout.write(f"{timezone.now():%H:%M:%S} cleaning up sub-labels for {user.email}")
            cleanup_runner.run_user(user)
