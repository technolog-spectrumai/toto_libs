"""
Export an OIDC manifest JSON file describing this app's relying-party requirements.

The manifest can be consumed by the portal's `import_oidc_manifest` command to
provision the SSO client without manual copy-pasting of credentials or settings.

Usage:
    python manage.py export_oidc_manifest
    python manage.py export_oidc_manifest --output /path/to/manifest.json
"""
import json
import sys

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Export an OIDC relying-party manifest JSON for portal provisioning."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output", "-o",
            default="-",
            help="Path to write the manifest JSON (default: stdout).",
        )

    def handle(self, *args, **options):
        try:
            cfg = apps.get_app_config("oidc_auth").get_config()
        except LookupError:
            raise CommandError("toto.oidc_auth is not in INSTALLED_APPS.")

        missing = [k for k in ("app_name", "client_id") if not cfg.get(k)]
        if missing:
            raise CommandError(
                f"OIDC_AUTH_CONFIG is missing required keys for manifest export: {missing}. "
                "Set them in sso.yaml or OIDC_AUTH_CONFIG."
            )

        manifest = {
            "schema_version": 1,
            "app": cfg["client_id"],
            "display_name": cfg["app_name"],
            "oidc_client": {
                "name": cfg["app_name"],
                "client_id": cfg["client_id"],
                "client_type": "confidential",
                "trusted": cfg["trusted"],
                "redirect_uris": cfg["redirect_uris"],
                "scopes": cfg["scopes"],
            },
        }

        output = json.dumps(manifest, indent=2)

        if options["output"] == "-":
            self.stdout.write(output)
        else:
            path = options["output"]
            with open(path, "w") as f:
                f.write(output)
                f.write("\n")
            self.stdout.write(self.style.SUCCESS(f"Manifest written to {path}"))
