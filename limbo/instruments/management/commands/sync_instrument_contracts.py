from django.core.management.base import BaseCommand

from toto.instruments.models import FinancialInstrument, InstrumentType


class Command(BaseCommand):
    help = "Generate or update Lapis Contract records for financial instruments."

    def add_arguments(self, parser):
        parser.add_argument(
            "--type",
            dest="instrument_type",
            choices=[t.value for t in InstrumentType],
            help="Only sync instruments of this type.",
        )
        parser.add_argument(
            "--reference",
            help="Only sync the instrument with this reference.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Generate YAML but do not persist any changes.",
        )

    def handle(self, *args, **options):
        from toto.instruments.lapis_contracts import (
            render_lapis_for_instrument,
            sync_contract_for_instrument,
        )

        qs = FinancialInstrument.objects.select_related("contract")
        if options["instrument_type"]:
            qs = qs.filter(instrument_type=options["instrument_type"])
        if options["reference"]:
            qs = qs.filter(reference=options["reference"])

        dry_run = options["dry_run"]
        ok = failed = skipped = 0

        for instrument in qs:
            ref = instrument.reference
            try:
                if dry_run:
                    code = render_lapis_for_instrument(instrument)
                    self.stdout.write(
                        f"  [dry] {ref}: {len(code)} chars of Lapis YAML"
                    )
                    ok += 1
                else:
                    contract = sync_contract_for_instrument(instrument)
                    verb = "updated" if instrument.contract_id else "created"
                    self.stdout.write(
                        self.style.SUCCESS(f"  ✓ {ref} → Contract '{contract.name}' ({verb})")
                    )
                    ok += 1
            except ValueError as exc:
                self.stdout.write(self.style.WARNING(f"  ⚠ {ref}: skipped — {exc}"))
                skipped += 1
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  ✗ {ref}: {exc}"))
                failed += 1

        suffix = " (dry run)" if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone{suffix}: {ok} synced, {skipped} skipped, {failed} failed."
            )
        )
