"""Delete expired relay messages (past their per-channel TTL).

Regular ``TelegraphMessage`` rows expire after ``channel.message_ttl_seconds``
(default 24h). Both at-rest and end-to-end (secure-on-send) rows share the same TTL.

faros has no celery, so run this from cron:
  */15 * * * *  python manage.py telegraph_purge_expired
"""
from django.core.management.base import BaseCommand

from toto.telegraph import vault


class Command(BaseCommand):
    help = "Delete relay messages past their TTL."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many rows would be deleted without deleting.",
        )

    def handle(self, *args, **options):
        from django.utils import timezone

        from ..models import TelegraphMessage

        if options["dry_run"]:
            count = TelegraphMessage.objects.filter(
                expires_at__lte=timezone.now()
            ).count()
            self.stdout.write(f"[dry-run] {count} expired message(s) would be deleted.")
            return

        deleted = vault.purge_expired()
        self.stdout.write(self.style.SUCCESS(f"Purged {deleted} expired message(s)."))
