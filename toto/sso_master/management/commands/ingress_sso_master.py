import os

from django.contrib.auth import get_user_model

from toto.ingress import IngressCommand


class Command(IngressCommand):
    help = "Seed dev SSO users for the portal."

    def process(self):
        User = get_user_model()
        users = [
            {"username": "sso1", "password": os.environ.get("SSO1_PASSWORD", "sso1")},
        ]
        for spec in users:
            user, created = User.objects.update_or_create(
                username=spec["username"],
                defaults={},
            )
            user.set_password(spec["password"])
            user.save(update_fields=["password"])
            verb = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{verb} user: {user.username}"))
