from django.contrib.auth.models import User
from toto.gervazy.models import KeyRing, RSAKeyPair, SecretPassword
from toto.core.ingress import IngressCommand
import os
import secrets


class Command(IngressCommand):
    help = "Seed Gervazy app with a demo KeyRing, RSA keypair, and example SecretPassword"

    def process(self):

        username = "admin"
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(f"User '{username}' not found. Create the user first.")
            )
            return

        # -----------------------------
        # Create or fetch KeyRing
        # -----------------------------
        keyring, created = KeyRing.objects.get_or_create(
            owner=user,
            label="Gervazy Demo Key",
            defaults={"salt": os.urandom(16)},
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created KeyRing: {keyring.label}"))
        else:
            self.stdout.write(f"KeyRing already exists: {keyring.label}")

        # -----------------------------
        # Create or fetch RSA Key Pair
        # -----------------------------
        rsa_pair, rsa_created = RSAKeyPair.objects.get_or_create(
            key_id="demo-keypair",
            issuer="https://gervazy.local",
        )

        if rsa_created:
            new_pair = RSAKeyPair.generate(
                key_id="demo-keypair",
                issuer="https://gervazy.local",
            )
            rsa_pair.private_key_pem = new_pair.private_key_pem
            rsa_pair.public_key_pem = new_pair.public_key_pem
            rsa_pair.save()

            self.stdout.write(
                self.style.SUCCESS(f"Created RSAKeyPair: {rsa_pair.key_id}")
            )
        else:
            self.stdout.write(f"RSAKeyPair already exists: {rsa_pair.key_id}")

        # -----------------------------
        # Optional: Link RSAKeyPair to KeyRing
        # -----------------------------
        if hasattr(keyring, "rsa_pair"):
            keyring.rsa_pair = rsa_pair
            keyring.save()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Linked RSAKeyPair '{rsa_pair.key_id}' to KeyRing '{keyring.label}'"
                )
            )

        # -----------------------------
        # Create Example SecretPassword
        # -----------------------------
        demo_passphrase = "demo-passphrase"
        demo_password = "SuperSecret123!"

        secret_password = SecretPassword(
            keyring=keyring,
            active=True,
        )
        secret_password.set_password(demo_password, demo_passphrase)
        secret_password.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"Created example SecretPassword {secret_password.id} "
                f"(encrypted with passphrase '{demo_passphrase}')"
            )
        )

        self.stdout.write(self.style.SUCCESS("Gervazy demo data seeded successfully."))
