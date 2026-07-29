"""Management command: grant_starting_gas

Funds every existing user's prepaid account with the starting gas grant.

Run this **before** enabling priced metering on a platform that already has
users, or they will all be refused everything: the signup grant only fires for
accounts created after it was wired up.

Idempotent — each user's grant is posted under the reference `gas-grant-<pk>`,
so a second run credits nobody twice. Safe to re-run after raising
GAS_STARTING_GRANT, though note it grants the *new* amount to users who never
got one, rather than topping up those who did.
"""
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from toto.assets.prepaid import grant_starting_gas


class Command(BaseCommand):
    help = "Grant the starting gas balance to every user who has not had one."

    def add_arguments(self, parser):
        parser.add_argument(
            "--amount",
            help="Override GAS_STARTING_GRANT for this run (display units).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be granted without moving anything.",
        )

    def handle(self, *args, **options):
        from toto.assets.models import Asset, LedgerTransaction

        unit = getattr(settings, "GAS_ASSET", "ASR")
        raw = options.get("amount") or getattr(settings, "GAS_STARTING_GRANT", "0")
        try:
            amount = Decimal(str(raw))
        except (InvalidOperation, TypeError):
            self.stdout.write(self.style.ERROR(f"✗ {raw!r} is not a valid amount."))
            return
        if amount <= 0:
            self.stdout.write(self.style.WARNING(
                f"⚠ grant is {amount} — nothing to do. Set GAS_STARTING_GRANT."
            ))
            return

        asset = Asset.objects.filter(unit_name=unit, active=True).first()
        if asset is None:
            self.stdout.write(self.style.ERROR(
                f"✗ no active {unit} asset — run ingress_assets first."
            ))
            return
        if not asset.reserve_account_id:
            self.stdout.write(self.style.ERROR(f"✗ {unit} has no reserve account to draw from."))
            return

        User = get_user_model()
        pending = [
            u for u in User.objects.all().order_by("pk")
            if not LedgerTransaction.objects.filter(reference=f"gas-grant-{u.pk}").exists()
        ]

        if options["dry_run"]:
            self.stdout.write(
                f"Would grant {amount} {unit} to {len(pending)} user(s), "
                f"totalling {amount * len(pending)} {unit}."
            )
            return

        granted = 0
        for user in pending:
            if grant_starting_gas(user, amount) is not None:
                granted += 1
                self.stdout.write(f"  +/✓ {amount} {unit} → {user}")
            else:
                # Almost always an exhausted reserve; stop rather than log
                # one failure per remaining user.
                self.stdout.write(self.style.WARNING(
                    f"  ⚠ could not grant to {user} — the {unit} reserve may be empty."
                ))
                break

        self.stdout.write(self.style.SUCCESS(
            f"✅  Granted {granted} user(s); {len(pending) - granted} still without gas."
        ))
