"""Management command: sync_budget_stream_types — seed/update BudgetStreamType rows."""
from django.core.management.base import BaseCommand

from toto.budget.registry import BudgetStreamRegistry


class Command(BaseCommand):
    help = "Create or update BudgetStreamType rows from the registry."

    def add_arguments(self, parser):
        parser.add_argument(
            "--deactivate-unknown",
            action="store_true",
            help="Deactivate stream types not present in registry.",
        )

    def handle(self, *args, **options):
        from toto.budget.models import BudgetStreamType

        specs = BudgetStreamRegistry.all()
        created_count = 0
        updated_count = 0
        known_codes = set()

        for spec in specs:
            known_codes.add(spec.code)
            obj, created = BudgetStreamType.objects.update_or_create(
                code=spec.code,
                defaults={
                    "name": spec.name,
                    "direction": spec.direction,
                    "namespace": spec.namespace,
                    "description": spec.description,
                    "is_system": spec.is_system,
                    "sort_order": spec.sort_order,
                    "is_active": True,
                },
            )
            if created:
                created_count += 1
                self.stdout.write(f"  Created: {spec.code}")
            else:
                updated_count += 1

        if options["deactivate_unknown"]:
            deactivated = BudgetStreamType.objects.exclude(code__in=known_codes).update(is_active=False)
            self.stdout.write(f"Deactivated {deactivated} unknown stream types.")

        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {created_count} created, {updated_count} updated."
            )
        )
