from django.contrib.auth.models import User
from toto.ingress import IngressCommand
from toto.gervazy.models import UserStrongbox


class Command(IngressCommand):
    help = "Seed Gervazy app with a demo UserStrongbox"

    def process(self):
        username = "admin"
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(f"User '{username}' not found. Create the user first.")
            )
            return

        strongbox, created = UserStrongbox.objects.get_or_create(
            owner=user,
            name="Gervazy Demo Strongbox",
            defaults={"notes": "Demo strongbox for development. Do not use in production."},
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created UserStrongbox: {strongbox.name}"))
        else:
            self.stdout.write(f"UserStrongbox already exists: {strongbox.name}")

        self.stdout.write(self.style.SUCCESS("Gervazy demo data seeded successfully."))
