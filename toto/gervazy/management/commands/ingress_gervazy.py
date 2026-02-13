from django.contrib.auth.models import User
from toto.gervazy.models import KeyRing, RSAKeyPair
from toto.core.ingress import IngressCommand
import os


class Command(IngressCommand):
    help = "Seed Gervazy app with a demo KeyRing and create dashboard item + RSA keypair"

    def process(self):

        username = "admin"
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(
                f"User {username} not found. Please create the user first."
            ))
            return

        # Create demo KeyRing
        keyring, created = KeyRing.objects.get_or_create(
            owner=user,
            label="Gervazy Demo Key",
            defaults={"salt": os.urandom(16)}
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created KeyRing: {keyring.label}"))
        else:
            self.stdout.write(f"KeyRing already exists: {keyring.label}")

        # Create or attach RSAKeyPair
        rsa_pair, rsa_created = RSAKeyPair.objects.get_or_create(
            key_id="demo-keypair",
            issuer="https://gervazy.local",
            defaults={}
        )

        if rsa_created:
            # Generate fresh key material
            new_pair = RSAKeyPair.generate("demo-keypair", "https://gervazy.local")
            rsa_pair.private_key_pem = new_pair.private_key_pem
            rsa_pair.public_key_pem = new_pair.public_key_pem
            rsa_pair.save()
            self.stdout.write(self.style.SUCCESS(f"Created RSAKeyPair: {rsa_pair.key_id}"))
        else:
            self.stdout.write(f"RSAKeyPair already exists: {rsa_pair.key_id}")

        # Optionally link the RSAKeyPair to the KeyRing if you have a relation
        if hasattr(keyring, "rsa_pair"):
            keyring.rsa_pair = rsa_pair
            keyring.save()
            self.stdout.write(self.style.SUCCESS(
                f"Linked RSAKeyPair {rsa_pair.key_id} to KeyRing {keyring.label}"
            ))

        self.stdout.write(self.style.SUCCESS("Gervazy demo data seeded successfully."))
