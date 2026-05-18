import json
from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from toto.sso_core.manifest import ManifestBundle, OIDCClientSpec


class Command(BaseCommand):
    help = "Export an OIDC relying-party manifest JSON for portal provisioning."

    def add_arguments(self, parser):
        parser.add_argument("--output", "-o", default="-", help="Path to write manifest JSON (default: stdout).")

    def handle(self, *args, **options):
        try:
            cfg = apps.get_app_config("sso_client").get_config()
        except LookupError:
            raise CommandError("toto.sso_client is not in INSTALLED_APPS.")

        missing = [k for k in ("app_name", "client_id") if not cfg.get(k)]
        if missing:
            raise CommandError(f"OIDC_AUTH_CONFIG missing keys for manifest export: {missing}")

        bundle = ManifestBundle(
            schema_version=1,
            app=cfg["client_id"],
            display_name=cfg["app_name"],
            oidc_client=OIDCClientSpec(
                name=cfg["app_name"],
                client_id=cfg["client_id"],
                client_type="confidential",
                trusted=cfg["trusted"],
                redirect_uris=cfg["redirect_uris"],
                scopes=cfg["scopes"],
            ),
        )

        output = bundle.to_json()
        if options["output"] == "-":
            self.stdout.write(output)
        else:
            with open(options["output"], "w") as f:
                f.write(output)
                f.write("\n")
            self.stdout.write(self.style.SUCCESS(f"Manifest written to {options['output']}"))
