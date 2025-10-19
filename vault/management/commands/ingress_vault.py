from django.contrib.auth.models import User
from vault.models import VaultFile, Bucket
from gervazy.models import KeyRing
from oya.ingress import IngressCommand
import os


class Command(IngressCommand):
    help = "Seed Vault app with demo data and create dashboard item"

    def process(self, _):
        # Create dashboard block
        self.create_dashboard_item(
            title="Vault",
            icon="fa-solid fa-vault",
            description="Secure storage for encrypted files and images",
            link="/vault/"
        )

        username = "admin"
        try:
            user = User.objects.get(username=username)
            self.stdout.write(f"Found demo user: {user.username}")
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR("Demo user 'vaultuser' not found. Please create the user first."))
            return

        # Create KeyRing
        keyring, _ = KeyRing.objects.get_or_create(
            owner=user,
            label="Demo Key",
            defaults={"salt": os.urandom(16)}
        )

        # Create Bucket
        bucket, _ = Bucket.objects.get_or_create(
            name="Demo Bucket",
            owner=user
        )

        # Create VaultFile entries (PDFs)
        for i in range(2):
            VaultFile.objects.create(
                owner=user,
                title=f"Secure PDF {i}",
                file="vault/encrypted_pdfs/secure_pdf.pdf",
                file_type="pdf",
                is_encrypted=True,
                bucket=bucket,
                notes="Demo encrypted PDF file"
            )

        self.stdout.write(self.style.SUCCESS("Vault demo data seeded successfully."))
