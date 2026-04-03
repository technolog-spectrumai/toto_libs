import os
import secrets
from datetime import datetime

from django.conf import settings
from django.core.files import File
from django.core.management import CommandError
from django.core.management.base import BaseCommand

from django.contrib.auth.models import User
from toto.core.models import Platform, Theme
from toto.gervazy.models import SecretKey, KeyRing


class Command(BaseCommand):
    help = "Create or update a Platform instance with theme, logo, metadata, and encrypted SecretKey"

    def add_arguments(self, parser):
        parser.add_argument("site_name", type=str, help="Platform site name")
        parser.add_argument("domain", type=str, help="Domain name for the platform")
        parser.add_argument("author", type=str, help="Platform Author")

        parser.add_argument("--active", type=bool, default=True)
        parser.add_argument("--theme_id", type=int)
        parser.add_argument("--secret_size", type=int, choices=[64, 128, 256], default=64)
        parser.add_argument("--passphrase", type=str, default="default-passphrase")

    def handle(self, *args, **options):
        site_name = options["site_name"]
        domain = options["domain"]
        author = options["author"]
        active = options["active"]
        theme_id = options.get("theme_id")
        secret_size = options["secret_size"]
        passphrase = options["passphrase"]
        publication_year = datetime.now().year

        # -----------------------------
        # Optional Theme
        # -----------------------------
        theme = None
        if theme_id:
            try:
                theme = Theme.objects.get(id=theme_id)
            except Theme.DoesNotExist:
                self.stderr.write(
                    self.style.WARNING(f"Theme with ID {theme_id} not found — continuing without theme.")
                )

        # -----------------------------
        # Ensure owner exists (admin)
        # -----------------------------
        try:
            owner = User.objects.get(username="admin")
        except User.DoesNotExist:
            raise CommandError("Admin user not found. Please create the admin user first.")

        # -----------------------------
        # Create or fetch KeyRing
        # -----------------------------
        keyring, created = KeyRing.objects.get_or_create(
            owner=owner,
            label=f"{site_name} Platform KeyRing",
            defaults={"salt": os.urandom(16)},
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created KeyRing: {keyring.label}"))
        else:
            self.stdout.write(f"Using existing KeyRing: {keyring.label}")

        # -----------------------------
        # Create encrypted SecretKey
        # -----------------------------
        raw_key = secrets.token_urlsafe(secret_size)

        secret = SecretKey(
            keyring=keyring,
            size=secret_size,
        )
        secret.set_key(raw_key, passphrase)
        secret.save()

        self.stdout.write(self.style.SUCCESS(f"Created encrypted SecretKey {secret.id}"))

        # -----------------------------
        # Create or Update Platform
        # -----------------------------
        platform, created = Platform.objects.update_or_create(
            domain=domain,
            defaults={
                "site_name": site_name,
                "secret": secret,
                "publication_year": publication_year,
                "active": active,
                "theme": theme,
                "author": author,
            },
        )

        # -----------------------------
        # Upload logo if missing
        # -----------------------------
        logo_path = os.path.abspath(os.path.join(settings.BASE_DIR, settings.PLATFORM_LOGO_PATH))

        if not os.path.exists(logo_path):
            raise CommandError(f"Logo file not found at {logo_path}")

        if not platform.logo:
            with open(logo_path, "rb") as f:
                platform.logo.save("platform_logo.png", File(f), save=True)

        platform.save()

        # -----------------------------
        # Output
        # -----------------------------
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created Platform: {platform.site_name}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Updated Platform: {platform.site_name}"))
