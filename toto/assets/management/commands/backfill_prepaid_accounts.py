"""
Management command: backfill_prepaid_accounts

Creates prepaid LedgerAccounts for all existing users who don't have one yet.
Safe to run multiple times (idempotent).
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from toto.assets.prepaid import get_or_create_prepaid_account


class Command(BaseCommand):
    help = "Create prepaid LedgerAccounts for all existing users."

    def handle(self, *args, **options):
        User = get_user_model()
        users = User.objects.all()
        created_count = 0
        for user in users:
            _, created = get_or_create_prepaid_account(user)
            if created:
                created_count += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {created_count} prepaid accounts created, "
                f"{users.count() - created_count} already existed."
            )
        )
