"""Initialize the telegraph at-rest encryption vault.

Creates the single *telegraph system strongbox* that holds every channel's data key
(DEK). Regular relay messages are stored encrypted under those DEKs and decrypted by
the server to serve readable history (the Discord model). End-to-end pin content is
never stored here.

This mirrors ``create_sso_signing_key``: the strongbox is unlocked server-side by
``TELEGRAPH_VAULT_PASSWORD`` (no human in the loop). Run once per deployment, before
serving chat; safe to re-run (idempotent).

Usage:
  TELEGRAPH_VAULT_PASSWORD=... python manage.py telegraph_init_vault
  python manage.py telegraph_init_vault --vault-password ... --owner telegraph-vault
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from toto.gervazy.crypto import GervazyCryptoSession
from toto.telegraph import vault

User = get_user_model()


class Command(BaseCommand):
    help = "Create the telegraph system strongbox for encrypted message history."

    def add_arguments(self, parser):
        parser.add_argument(
            "--vault-password",
            default="",
            help="Strongbox password (overrides TELEGRAPH_VAULT_PASSWORD setting).",
        )
        parser.add_argument(
            "--owner",
            default=vault.SYSTEM_OWNER_USERNAME,
            help="Username of the service account that owns the strongbox.",
        )

    def handle(self, *args, **options):
        password = (
            options["vault_password"]
            or (getattr(settings, "TELEGRAPH_VAULT_PASSWORD", "") or "").strip()
        )
        if not password:
            raise CommandError(
                "No vault password. Set TELEGRAPH_VAULT_PASSWORD or pass --vault-password."
            )

        existing = vault.system_strongbox()
        if existing is not None:
            # Verify the password unlocks it (fail loudly on mismatch) and ensure a DEK exists.
            try:
                session = GervazyCryptoSession(existing, password)
            except Exception as exc:  # InvalidTag etc.
                raise CommandError(
                    f"Telegraph strongbox exists but the password does not unlock it: {exc}"
                )
            if not existing.data_keys.filter(state="active").exists():
                session.create_data_key()
            self.stdout.write(
                self.style.SUCCESS("Telegraph vault already initialized — OK.")
            )
            return

        owner, _ = User.objects.get_or_create(
            username=options["owner"],
            defaults={"is_active": False},  # service account; cannot log in
        )

        self.stdout.write(f"Creating telegraph system strongbox: {vault.SYSTEM_STRONGBOX_NAME!r}")
        GervazyCryptoSession.initialize_strongbox(
            owner, vault.SYSTEM_STRONGBOX_NAME, password
        )
        vault.clear_cache()
        self.stdout.write(self.style.SUCCESS("Telegraph vault initialized."))
        self.stdout.write(self.style.WARNING(
            "Keep TELEGRAPH_VAULT_PASSWORD secret and never commit it. Losing it makes "
            "stored message history permanently unreadable."
        ))
