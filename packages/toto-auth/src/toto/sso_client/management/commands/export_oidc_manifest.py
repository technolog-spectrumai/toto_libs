from django.core.management.base import BaseCommand, CommandError
from toto.sso_core.manifest import ManifestBundle, OIDCClientSpec
from toto.sso_client.models import OIDCProviderConfig


class Command(BaseCommand):
    help = "Export an OIDC relying-party manifest JSON for portal provisioning."

    def add_arguments(self, parser):
        parser.add_argument("--output", "-o", default="-", help="Path to write manifest JSON (default: stdout).")

    def handle(self, *args, **options):
        record = OIDCProviderConfig.objects.filter(active=True).order_by("-imported_at").first()
        if not record:
            raise CommandError(
                "No active OIDCProviderConfig found. "
                "Run ingress_sso_client or import a connection bundle first."
            )

        missing = [f for f, v in [("app_name", record.app_name), ("client_id", record.client_id)] if not v]
        if missing:
            raise CommandError(f"OIDCProviderConfig is missing: {missing}")

        bundle = ManifestBundle(
            schema_version=1,
            app=record.client_id,
            display_name=record.app_name,
            oidc_client=OIDCClientSpec(
                name=record.app_name,
                client_id=record.client_id,
                client_type="confidential",
                trusted=record.trusted,
                redirect_uris=record.redirect_uris_list(),
                scopes=record.scopes,
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
