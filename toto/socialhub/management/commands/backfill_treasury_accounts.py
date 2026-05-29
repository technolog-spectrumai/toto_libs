"""
Management command: backfill_treasury_accounts

Creates treasury LedgerAccounts for all existing communities that don't have one.
Safe to run multiple times (idempotent).
"""
from django.core.management.base import BaseCommand

from toto.socialhub.models import Community
from toto.socialhub.treasury import get_or_create_treasury_account


class Command(BaseCommand):
    help = "Create treasury LedgerAccounts for all existing communities."

    def handle(self, *args, **options):
        communities = Community.objects.all()
        created_count = 0
        for community in communities:
            _, created = get_or_create_treasury_account(community)
            if created:
                created_count += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {created_count} treasury accounts created, "
                f"{communities.count() - created_count} already existed."
            )
        )
