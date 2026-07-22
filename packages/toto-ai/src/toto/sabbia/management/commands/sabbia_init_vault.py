"""Initialize the sabbia credential vault.

Creates the single *sabbia system strongbox* that holds agent API keys (e.g. the
OpenAI key for the Steven bot). The strongbox is unlocked server-side by
``SABBIA_VAULT_PASSWORD`` (no human in the loop). Run once per deployment, before
seeding agents; safe to re-run (idempotent). Mirrors ``create_sso_signing_key``.

Usage:
  SABBIA_VAULT_PASSWORD=... python manage.py sabbia_init_vault
  python manage.py sabbia_init_vault --vault-password ... --owner sabbia-vault
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from toto.gervazy.crypto import GervazyCryptoSession
from toto.sabbia import vault

User = get_user_model()


class Command(BaseCommand):
    help = "Create the sabbia system strongbox for encrypted agent credentials."

    def add_arguments(self, parser):
        parser.add_argument(
            "--vault-password",
            default="",
            help="Strongbox password (overrides SABBIA_VAULT_PASSWORD setting).",
        )
        parser.add_argument(
            "--owner",
            default=vault.SYSTEM_OWNER_USERNAME,
            help="Username of the service account that owns the strongbox.",
        )

    def handle(self, *args, **options):
        password = (
            options["vault_password"]
            or (getattr(settings, "SABBIA_VAULT_PASSWORD", "") or "").strip()
        )
        if not password:
            raise CommandError(
                "No vault password. Set SABBIA_VAULT_PASSWORD or pass --vault-password."
            )

        existing = vault.system_strongbox()
        if existing is not None:
            try:
                session = GervazyCryptoSession(existing, password)
            except Exception as exc:  # InvalidTag etc.
                raise CommandError(
                    f"Sabbia strongbox exists but the password does not unlock it: {exc}"
                )
            if not existing.data_keys.filter(state="active").exists():
                session.create_data_key()
            self.stdout.write(self.style.SUCCESS("Sabbia vault already initialized — OK."))
            return

        owner, _ = User.objects.get_or_create(
            username=options["owner"],
            defaults={"is_active": False},  # service account; cannot log in
        )

        self.stdout.write(
            f"Creating sabbia system strongbox: {vault.SYSTEM_STRONGBOX_NAME!r}"
        )
        GervazyCryptoSession.initialize_strongbox(
            owner, vault.SYSTEM_STRONGBOX_NAME, password
        )
        vault.clear_cache()
        self.stdout.write(self.style.SUCCESS("Sabbia vault initialized."))
        self.stdout.write(self.style.WARNING(
            "Keep SABBIA_VAULT_PASSWORD secret and never commit it. Losing it makes "
            "stored agent credentials permanently unreadable."
        ))
