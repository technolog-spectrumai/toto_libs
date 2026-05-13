from django.contrib.auth.models import User
from toto.core.ingress import IngressCommand
from toto.gervazy.models import UserVault


class Command(IngressCommand):
    help = "Seed Gervazy app with a demo UserVault"

    def process(self):
        username = "admin"
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(f"User '{username}' not found. Create the user first.")
            )
            return

        vault, created = UserVault.objects.get_or_create(
            owner=user,
            name="Gervazy Demo Vault",
            defaults={"notes": "Demo vault for development. Do not use in production."},
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created UserVault: {vault.name}"))
        else:
            self.stdout.write(f"UserVault already exists: {vault.name}")

        self.stdout.write(self.style.SUCCESS("Gervazy demo data seeded successfully."))
