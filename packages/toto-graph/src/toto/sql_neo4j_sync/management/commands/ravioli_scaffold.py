"""
ravioli_scaffold — generate draft ravioli/graph/*.yaml files from Django models.

For each app label given (or all installed toto apps if none are given),
the command introspects every model that has a 'uid' field (DomainEntity
subclasses) and writes a YAML scaffold to ravioli/graph/<app>.yaml.

The output is a STARTING POINT.  Every "relation" name is a TODO placeholder
and cross-app to_label values may need adjusting.  Run ravioli_check
afterwards to verify the result.

Usage:
    manage.py ravioli_scaffold                        # scaffold all toto apps
    manage.py ravioli_scaffold toto.kanban toto.bento # specific apps
    manage.py ravioli_scaffold --dry-run              # print without writing
    manage.py ravioli_scaffold --overwrite            # replace existing files
"""

from django.apps import apps as django_apps
from django.core.management.base import BaseCommand

from toto.sql_neo4j_sync.loader import GRAPH_DIR
from toto.sql_neo4j_sync.scaffold import config_to_yaml, scaffold_app


# Third-party / Django built-in app names to always skip.
_SKIP_PREFIXES = ("django.", "rest_framework", "corsheaders", "channels")
_SKIP_NAMES = {
    "markdownx", "trix_editor", "jsoneditor", "colorfield", "reversion",
    "django_prometheus", "django_jsonform", "django_json_widget",
    "toto.ravioli",  # skip self — ravioli has no DomainEntity models to scaffold
}


def _is_toto_app(name):
    return not any(name.startswith(p) for p in _SKIP_PREFIXES) and name not in _SKIP_NAMES


class Command(BaseCommand):
    help = "Generate draft ravioli/graph/*.yaml files from Django model introspection"

    def add_arguments(self, parser):
        parser.add_argument(
            "app_labels",
            nargs="*",
            metavar="APP",
            help=(
                "Django app labels to scaffold "
                "(default: all installed apps that are not Django/third-party)"
            ),
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            default=False,
            help="Replace existing YAML files (default: skip if file already exists)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Print generated YAML to stdout instead of writing files",
        )

    def handle(self, *args, **options):
        app_labels = options["app_labels"]
        overwrite = options["overwrite"]
        dry_run = options["dry_run"]

        if not app_labels:
            app_labels = sorted(
                cfg.name
                for cfg in django_apps.get_app_configs()
                if _is_toto_app(cfg.name)
            )
            self.stdout.write(
                f"No apps specified — scaffolding {len(app_labels)} app(s):\n"
                + "  " + "\n  ".join(app_labels)
            )

        if dry_run:
            self.stdout.write(
                self.style.WARNING("Dry-run mode — no files will be written.\n")
            )

        written = 0
        skipped = 0
        empty = 0

        for label in app_labels:
            result = self._scaffold_one(label, overwrite=overwrite, dry_run=dry_run)
            if result == "written":
                written += 1
            elif result == "skipped":
                skipped += 1
            else:
                empty += 1

        if not dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\nDone. Written: {written}  Skipped: {skipped}  "
                    f"No graphable models: {empty}"
                )
            )

    def _scaffold_one(self, app_label, overwrite, dry_run):
        try:
            config = scaffold_app(app_label)
        except LookupError:
            self.stderr.write(self.style.ERROR(f"  {app_label}: unknown app — skipping"))
            return "skipped"

        if config is None:
            self.stdout.write(
                f"  {app_label}: no DomainEntity models found — skipping"
            )
            return "empty"

        n_nodes = len(config["nodes"])
        n_links = len(config["links"])
        file_name = app_label.split(".")[-1] + ".yaml"
        out_path = GRAPH_DIR / file_name
        yaml_text = config_to_yaml(config)

        if dry_run:
            self.stdout.write(
                self.style.HTTP_INFO(f"\n{'─' * 60}\n{file_name}  "
                                     f"({n_nodes} node(s), {n_links} link(s))\n{'─' * 60}")
            )
            self.stdout.write(yaml_text)
            return "written"

        if out_path.exists() and not overwrite:
            self.stdout.write(
                self.style.WARNING(
                    f"  {file_name}: already exists — skipping "
                    f"(use --overwrite to replace)"
                )
            )
            return "skipped"

        out_path.write_text(yaml_text, encoding="utf-8")
        self.stdout.write(
            self.style.SUCCESS(
                f"  {file_name}: wrote {n_nodes} node(s), {n_links} link(s)"
            )
        )
        return "written"
