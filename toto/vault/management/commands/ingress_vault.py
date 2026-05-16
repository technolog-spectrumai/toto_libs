from django.contrib.auth.models import User
from django.core.management.base import CommandError
from django.utils.text import slugify
from toto.ingress import IngressCommand
from toto.gervazy.models import UserStrongbox
from toto.vault.models import VaultFile, Bucket, FileGateway
import os
import random


class Command(IngressCommand):
    help = "Seed Vault demo data including buckets, files, and upload gateways"

    def process(self):
        # -----------------------------
        # Dashboard Item (Vault only)
        # -----------------------------

        if not self.full:
            return

        # -----------------------------
        # Users
        # -----------------------------
        try:
            user = User.objects.get(username="admin")
            self.stdout.write(f"Found demo user: {user.username}")
        except User.DoesNotExist:
            raise CommandError("Demo user 'admin' not found. Please create the user first.")

        users = list(User.objects.all())
        if not users:
            raise CommandError("No users found. Create at least one user first.")

        # -----------------------------
        # Vault: UserStrongbox
        # -----------------------------
        keyring, _ = UserStrongbox.objects.get_or_create(
            owner=user,
            name="Demo Key",
        )

        # -----------------------------
        # Vault: Bucket
        # -----------------------------
        bucket, _ = Bucket.objects.get_or_create(
            name="Demo Bucket",
            owner=user,
            slug="demo-bucket",
        )

        # -----------------------------
        # Vault: Demo Files
        # -----------------------------
        for i in range(3):
            VaultFile.objects.create(
                owner=user,
                title=f"Secure PDF {i}",
                file=f"vault/encrypted_pdfs/secure_pdf_{i}.pdf",
                file_type="pdf",
                is_encrypted=True,
                bucket=bucket,
                notes="Demo encrypted PDF file"
            )

        self.stdout.write(self.style.SUCCESS("Vault demo data seeded."))

        # -----------------------------
        # Vault: Gateways
        # -----------------------------
        buckets = list(Bucket.objects.all())
        if not buckets:
            raise CommandError("No buckets found. Create some buckets first.")

        self.stdout.write(f"Found {len(buckets)} buckets. Creating gateways...")

        # Pick 3–5 random buckets
        sample_count = min(len(buckets), random.randint(3, 5))
        selected_buckets = random.sample(buckets, sample_count)

        for bucket in selected_buckets:

            # FileGateway is OneToOne with Bucket
            gateway, created = FileGateway.objects.get_or_create(
                bucket=bucket,
                defaults={
                    "name": slugify(f"gateway for {bucket.name}"),
                    "description": f"Upload gateway for bucket '{bucket.name}'",
                    "make_public": True
                }
            )

            # Assign 1–3 random allowed users
            allowed = random.sample(users, random.randint(1, min(3, len(users))))
            gateway.allowed_users.set(allowed)

            gateway.save()

            if created:
                self.stdout.write(self.style.SUCCESS(f"Created gateway for bucket: {bucket.name}"))
            else:
                self.stdout.write(f"Gateway already exists for bucket: {bucket.name}")

        self.stdout.write(self.style.SUCCESS("Vault gateways created successfully."))
        self.stdout.write(self.style.SUCCESS("All demo data seeded."))
