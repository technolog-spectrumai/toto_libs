"""
Import an OIDC manifest JSON produced by `export_oidc_manifest` and provision
the relying party on this portal.

Usage:
    python manage.py import_oidc_manifest manifest.json
    python manage.py import_oidc_manifest manifest.json --force-recreate
    python manage.py import_oidc_manifest manifest.json --raw-secret mysecret
"""
import json

from django.core.management.base import BaseCommand, CommandError

from toto.sso.provisioning import RelyingPartyProvisioningError, create_relying_party


class Command(BaseCommand):
    help = "Provision an SSO relying party from an OIDC manifest JSON file."

    def add_arguments(self, parser):
        parser.add_argument("manifest", help="Path to the manifest JSON file.")
        parser.add_argument(
            "--force-recreate",
            action="store_true",
            help="Delete and recreate the relying party if it already exists.",
        )
        parser.add_argument(
            "--raw-secret",
            default=None,
            help="Client secret to set (dev/scripted provisioning). Omit to auto-generate.",
        )

    def handle(self, *args, **options):
        try:
            with open(options["manifest"]) as f:
                manifest = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"Cannot read manifest: {exc}") from exc

        version = manifest.get("schema_version")
        if version != 1:
            raise CommandError(f"Unsupported manifest schema_version: {version!r}")

        client_cfg = manifest.get("oidc_client")
        if not client_cfg:
            raise CommandError("Manifest is missing the 'oidc_client' section.")

        try:
            provisioned = create_relying_party(
                name=client_cfg["name"],
                redirect_uris=client_cfg.get("redirect_uris", []),
                trusted=client_cfg.get("trusted", False),
                public=client_cfg.get("client_type") == "public",
                scopes=client_cfg.get("scopes", "openid email profile"),
                client_id=client_cfg.get("client_id"),
                raw_secret=options["raw_secret"],
                force_recreate=options["force_recreate"],
            )
        except RelyingPartyProvisioningError as exc:
            raise CommandError(str(exc)) from exc

        rp = provisioned.relying_party
        self.stdout.write(self.style.SUCCESS(f"Relying party provisioned: {rp.name}"))
        self.stdout.write(f"  client_id:    {rp.client_id}")
        self.stdout.write(f"  trusted:      {rp.trusted}")
        self.stdout.write(f"  redirect_uris: {rp.redirect_uri_list()}")
        if provisioned.client_secret:
            self.stdout.write(f"  client_secret: {provisioned.client_secret}")
            self.stdout.write(self.style.WARNING(
                "  Store this secret now — it is hashed and cannot be shown again."
            ))
        else:
            self.stdout.write("  client_secret: none (public client, use PKCE)")
