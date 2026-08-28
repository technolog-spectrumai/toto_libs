"""Verify the audit chain from the command line, for cron and for a human.

Exits non-zero when the chain is broken, so a cron line that pipes failure to
mail actually reports something.
"""

from django.core.management.base import BaseCommand, CommandError

from toto.audit.services import verify_chain


class Command(BaseCommand):
    help = "Walk the audit chain and confirm every record still verifies."

    def handle(self, *args, **options):
        result = verify_chain()
        if result.ok:
            self.stdout.write(self.style.SUCCESS(
                f"Audit chain is healthy — {result.checked} record(s) verified."
            ))
            return None
        raise CommandError(
            f"Audit chain is BROKEN at sequence {result.first_bad_sequence}: {result.detail} "
            f"({result.checked} record(s) verified before the break.)"
        )
