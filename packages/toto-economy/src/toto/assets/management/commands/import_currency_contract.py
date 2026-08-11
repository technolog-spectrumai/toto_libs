"""Branch side: accept the currency the master assigned, and mirror its asset.

Refuses a descriptor for another platform, a serial at or below the one held,
and a genesis signature that does not verify. Nothing is written until all
three hold.
"""

import json

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Import a signed currency contract from the master platform."

    def add_arguments(self, parser):
        parser.add_argument("path", help="The descriptor file to import.")

    def handle(self, *args, **options):
        from toto.assets.contracts import import_contract

        try:
            with open(options["path"]) as handle:
                descriptor = json.load(handle)
        except (OSError, ValueError) as exc:
            raise CommandError(f"Cannot read {options['path']}: {exc}") from exc

        try:
            contract = import_contract(descriptor)
        except Exception as exc:  # noqa: BLE001 - reported to the operator
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(
            f"this platform now bills in {contract.asset.unit_name} "
            f"(serial {contract.serial})"))
        self.stdout.write(f"  identity: {contract.currency_hash}")
        self.stdout.write(
            "  the asset is a mirror: it cannot be issued, minted or traded here")
