"""
ravioli_check — validate all ravioli/graph/*.yaml configs without
connecting to Neo4j.

Checks:
  - Each 'app' is a known Django app label.
  - Each 'model' can be imported as a Django model.
  - Every field referenced in 'fields' exists on its model.
  - Every from_label / to_label referenced in 'links' is declared
    somewhere across the full YAML corpus (cross-file links are fine).
  - For junction-model links, from_field / to_field exist on the
    via_model.

Exit code 1 if any errors are found.
"""

from django.core.management.base import BaseCommand

from toto.sql_neo4j_sync.loader import load_all_configs, validate_configs


class Command(BaseCommand):
    help = "Validate ravioli graph YAML configs (no Neo4j connection required)"

    def handle(self, *args, **options):
        self.stdout.write("Loading graph configs from ravioli/graph/ …")
        configs = load_all_configs()
        self.stdout.write(f"  Loaded {len(configs)} file(s): "
                          f"{', '.join(c['_source'] for c in configs)}")

        errors = validate_configs(configs)

        if errors:
            self.stderr.write(
                self.style.ERROR(f"\n{len(errors)} error(s) found:\n")
            )
            for err in errors:
                self.stderr.write(self.style.ERROR(f"  • {err}"))
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS("\nAll configs are valid."))
