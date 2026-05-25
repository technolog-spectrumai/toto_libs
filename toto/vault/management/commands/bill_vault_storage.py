"""
bill_vault_storage — post one storage.mb_hour usage record per vault user.

Run once per billing tick (hourly via cron or management command):
  python manage.py bill_vault_storage

Each run posts the user's *current* total vault storage as a single mb_hour
charge. If a user has no files, they are skipped.

Options:
  --user  <pk|username>   Bill a single user only.
  --dry-run               Print what would be charged without writing anything.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Post hourly vault storage (mb_hour) billing charges for all users."

    def add_arguments(self, parser):
        parser.add_argument("--user", help="Bill a single user by pk or username.")
        parser.add_argument("--dry-run", action="store_true", help="Show charges without posting.")

    def handle(self, *args, **options):
        from toto.vault.billing import charge_storage_snapshot, get_user_storage_summary

        User = get_user_model()
        dry_run = options["dry_run"]

        if options["user"]:
            ident = options["user"]
            qs = User.objects.filter(pk=ident) if ident.isdigit() else User.objects.filter(username=ident)
            if not qs.exists():
                self.stderr.write(f"User not found: {ident}")
                return
            users = list(qs)
        else:
            users = list(
                User.objects.filter(vaultfile__isnull=False).distinct().order_by("pk")
            )

        if not users:
            self.stdout.write("No vault users found.")
            return

        billed = skipped = failed = 0

        for user in users:
            summary = get_user_storage_summary(user)
            if summary["file_count"] == 0 or summary["total_bytes"] == 0:
                skipped += 1
                continue

            label = f"{user.username} ({summary['file_count']} files, {summary['total_mb']:.3f} MB)"

            if dry_run:
                self.stdout.write(f"  [dry-run] would bill {label}")
                billed += 1
                continue

            from toto.vault.models import Bucket
            from decimal import Decimal
            quota_mb = (
                Bucket.objects.filter(files__owner=user)
                .exclude(storage_quota_mb__isnull=True)
                .values_list("storage_quota_mb", flat=True)
                .first()
            )
            record = charge_storage_snapshot(
                user,
                quota_mb=Decimal(str(quota_mb)) if quota_mb else None,
            )
            if record:
                self.stdout.write(f"  ✓ billed {label}")
                billed += 1
            else:
                self.stdout.write(self.style.WARNING(f"  ✗ failed/skipped {label}"))
                failed += 1

        if dry_run:
            self.stdout.write(self.style.SUCCESS(
                f"[dry-run] would bill {billed} users, {skipped} with no storage."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Done — billed {billed}, failed {failed}, skipped (no files) {skipped}."
            ))
