import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from accounts import runner
from accounts.models import GoogleConnection, User

INTERVAL = timedelta(minutes=5)
POLL_SECONDS = 10


class Command(BaseCommand):
    help = "Run the agent for every enabled user every 5 minutes, and on 'Run now'."

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
            time.sleep(POLL_SECONDS)
