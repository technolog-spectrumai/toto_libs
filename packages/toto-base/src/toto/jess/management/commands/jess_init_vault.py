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

        # Prove the passphrase really opens it. A wrong one is NOT detected when the
        # session is constructed — only the KDF runs there — so it would otherwise
        # surface much later as an InvalidTag on a send.
        if vault.is_available():
            self.stdout.write(self.style.SUCCESS("Vault opens with the configured passphrase."))
        else:
            self.stdout.write(self.style.ERROR(
                "Vault did NOT open. JESS_VAULT_PASSWORD does not match this strongbox; "
                "stored passwords cannot be decrypted and must be re-entered."
            ))

        total = EmailProvider.objects.count()
        self.stdout.write(f"{total} email provider(s) configured.")
        self.stdout.write(jess_status.describe())
