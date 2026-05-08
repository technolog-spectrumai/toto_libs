from django.contrib.auth.models import User
from toto.core.models import EnvironmentVariable
from toto.gervazy.models import KeyRing, RSAKeyPair, SecretKey, SecretPassword
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
            name="Gervazy Demo Key",
            defaults={"salt": os.urandom(16)},
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created KeyRing: {keyring.name}"))
        else:
            self.stdout.write(f"KeyRing already exists: {keyring.name}")

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
                    f"Linked RSAKeyPair '{rsa_pair.key_id}' to KeyRing '{keyring.name}'"
                )
            )

        demo_passphrase = "demo-passphrase"

        env_var, env_created = EnvironmentVariable.objects.get_or_create(
            name="GERVAZY_DEMO_PASSPHRASE",
            defaults={
                "active": True,
                "notes": "Demo passphrase used to unlock the Gervazy demo SecretKey.",
            },
        )
        if not env_created:
            env_var.active = True
            env_var.save(update_fields=["active", "updated_at"])
        env_var.set_value(demo_passphrase)

        secret_key = SecretKey.objects.filter(keyring=keyring, active=True).first()
        if not secret_key:
            secret_key = SecretKey(keyring=keyring, size=64, active=True)
            secret_key.set_key(secrets.token_urlsafe(64), demo_passphrase)
            secret_key.save()

        secret_password = SecretPassword.objects.filter(name="gervazy-demo-password").first()
        if not secret_password:
            secret_password = SecretPassword(
                name="gervazy-demo-password",
                secret_key=secret_key,
                environment_variable=env_var,
                active=True,
            )
            secret_password.save()
        else:
            secret_password.secret_key = secret_key
            secret_password.environment_variable = env_var
            secret_password.active = True
            secret_password.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"Created example SecretPassword {secret_password.id} "
                f"(unlocked via {env_var.name})"
            )
        )

        self.stdout.write(self.style.SUCCESS("Gervazy demo data seeded successfully."))
