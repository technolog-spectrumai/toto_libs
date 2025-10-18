from django.contrib.auth.models import User
from gervazy.models import KeyRing
from oya.ingress import IngressCommand
import os


class Command(IngressCommand):
    help = "Seed Gervazy app with a demo KeyRing and create dashboard item"

    def process(self, _):

        username = "admin"
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"User {username} not found. Please create the user first."))
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

        self.stdout.write(self.style.SUCCESS("Gervazy demo data seeded successfully."))
