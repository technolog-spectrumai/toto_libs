from django.utils.text import slugify
from django.contrib.auth.models import User
from vault.models import VaultPdf, KeyRing
from oya.ingress import IngressCommand
import random
import os

class Command(IngressCommand):
    help = "Seed Vault app with demo data and create dashboard item"

    def process(self, _):
        # Dashboard block
        self.create_dashboard_item(
            title="Vault",
            icon="fa-lock",
            description="Secure storage for encrypted files and PDFs",
            link="/vault/"
        )

        # Create demo user
        user, _ = User.objects.get_or_create(username="vaultuser", defaults={"email": "vault@example.com"})

        # Create KeyRing
        salt = os.urandom(16)
        keyring = KeyRing.objects.create(owner=user, label="Demo Key", salt=salt)

        # Create VaultPdf entries
        for i in range(2):
            VaultPdf.objects.create(
                owner=user,
                title=f"Secure PDF {i}",
                file="vault/encrypted_pdfs/secure_pdf.pdf",
                keyring=keyring,
                is_encrypted=True
            )

        self.stdout.write(self.style.SUCCESS("Vault demo data seeded successfully."))
