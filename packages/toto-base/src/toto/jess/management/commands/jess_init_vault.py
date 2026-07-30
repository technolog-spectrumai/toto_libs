"""Create Jess's strongbox explicitly, and report what it finds.

Not required: ``jess/vault.py`` creates the strongbox on first use, because the
passphrase is already guaranteed by ``deploy.py`` and there is nothing for a human to
decide. This command exists to do it *deliberately* and to answer "is mail actually
configured?" from a shell — which is the question you have when a reset email did not
arrive.

    manage.py jess_init_vault
"""
from django.core.management.base import BaseCommand

from toto.jess import status as jess_status
from toto.jess import vault
from toto.jess.models import EmailProvider


class Command(BaseCommand):
    help = "Initialise Jess's encryption strongbox and report the mail configuration."

    def handle(self, *args, **options):
        try:
            vault.load_vault_password()
        except vault.VaultUnavailable as exc:
            # A warning, not an error: a host may legitimately install jess before the
            # passphrase reaches its environment, and failing here would fail ingress_all.
            self.stdout.write(self.style.WARNING(f"⚠ {exc}"))
            return

        existing = vault.system_strongbox()
        strongbox = vault.ensure_strongbox()
        verb = "Found" if existing else "Created"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} strongbox '{strongbox.name}' (owner {strongbox.owner.get_username()})"
        ))

        # Prove the passphrase really unwraps this strongbox's keys.
        #
        # `vault.is_available()` is NOT enough and must not be used here: it only
        # constructs a session, and constructing one just runs the KDF
        # (gervazy/crypto.py) — a wrong passphrase derives a different UKEK perfectly
        # happily and is not detected until something tries to unwrap. That is exactly
        # the failure this command exists to surface, so it has to go one step further
        # and actually decrypt.
        #
        # The strongest available check is reading a secret that is already stored,
        # because that is the thing a send will do. With none stored there is nothing to
        # unwrap yet, and saying so is more useful than implying a check that did not
        # happen.
        stored = (
            EmailProvider.objects
            .exclude(secret__isnull=True)
            .select_related("secret")
            .order_by("-active", "-updated_at")
            .first()
        )
        if stored is None:
            self.stdout.write(
                "No password is stored yet, so there is nothing to decrypt — the "
                "passphrase cannot be verified until one is saved in the admin."
            )
        else:
            try:
                vault.read_secret(stored.secret)
                self.stdout.write(self.style.SUCCESS(
                    f"Decrypted the stored password for '{stored.label}' — "
                    "JESS_VAULT_PASSWORD is correct."
                ))
            except Exception as exc:                        # noqa: BLE001
                self.stdout.write(self.style.ERROR(
                    f"Could NOT decrypt the stored password for '{stored.label}': {exc}\n"
                    "JESS_VAULT_PASSWORD does not match the passphrase these secrets "
                    "were stored under. They are unrecoverable and must be re-entered."
                ))

        total = EmailProvider.objects.count()
        self.stdout.write(f"{total} email provider(s) configured.")
        self.stdout.write(jess_status.describe())
