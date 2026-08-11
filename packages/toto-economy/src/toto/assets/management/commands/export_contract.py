"""Master side: write the signed contract that grants a platform its currency.

The descriptor carries the asset's whole genesis document, so importing it on
the branch also mirrors the asset. One file, one act — and because the document
is signed, the file can travel by any means at all.
"""

import json

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Export a signed currency contract for a branch platform."

    def add_arguments(self, parser):
        parser.add_argument("--node", required=True,
                            help="The platform this contract is for.")
        parser.add_argument("--asset", required=True,
                            help="Ticker of the asset it will bill in.")
        parser.add_argument("--out", default="",
                            help="File to write. Defaults to stdout.")

    def handle(self, *args, **options):
        from toto.assets.contracts import assign_contract
        from toto.assets.models import Asset

        asset = Asset.objects.filter(unit_name=options["asset"]).first()
        if asset is None:
            raise CommandError(f"No asset named {options['asset']!r} here.")

        try:
            contract = assign_contract(node=options["node"], asset=asset)
        except Exception as exc:  # noqa: BLE001 - reported to the operator
            raise CommandError(str(exc)) from exc

        blob = json.dumps(contract.payload, indent=2, sort_keys=True)
        if options["out"]:
            with open(options["out"], "w") as handle:
                handle.write(blob)
            self.stdout.write(self.style.SUCCESS(
                f"contract serial {contract.serial} for {contract.node_id} "
                f"→ {options['out']}"))
        else:
            self.stdout.write(blob)
