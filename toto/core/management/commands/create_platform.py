import os
import uuid
import secrets
from datetime import datetime

from django.conf import settings
from django.core.files import File
from django.core.management import CommandError
from django.core.management.base import BaseCommand

from django.contrib.auth.models import User
from toto.core.models import Platform, Theme, RSAKeyPair
from toto.gervazy.models import SecretKey, KeyRing


class Command(BaseCommand):
    help = "Create or update a Platform with theme, logo, metadata, SecretKey, and RSA keypair"

    def add_arguments(self, parser):
        parser.add_argument("site_name", type=str)
        parser.add_argument("domain", type=str)
        parser.add_argument("author", type=str)

        parser.add_argument("--active", type=bool, default=True)
        parser.add_argument("--theme_id", type=int)

        # symmetric key
        parser.add_argument("--secret_size", type=int, choices=[64, 128, 256], default=64)
        parser.add_argument("--passphrase", type=str, default="default-passphrase")

        # RSA keypair
        parser.add_argument("--rsa_keypair_id", type=str, help="Existing RSA keypair ID")

    def handle(self, *args, **options):
        site_name = options["site_name"]
        domain = options["domain"]
        author = options["author"]
        active = options["active"]
        theme_id = options.get("theme_id")

        secret_size = options["secret_size"]
        passphrase = options["passphrase"]
        rsa_keypair_id = options.get("rsa_keypair_id")

        publication_year = datetime.now().year

        # -----------------------------
        # Theme
        # -----------------------------
        theme = None
        if theme_id:
            try:
                theme = Theme.objects.get(id=theme_id)
            except Theme.DoesNotExist:
                self.stderr.write(self.style.WARNING(f"Theme {theme_id} not found."))

        # -----------------------------
        # Owner (admin)
        # -----------------------------
        try:
            owner = User.objects.get(username="admin")
        except User.DoesNotExist:
            raise CommandError("Admin user not found.")

        # -----------------------------
        # KeyRing (for SecretKey)
        # -----------------------------
        keyring, created = KeyRing.objects.get_or_create(
            owner=owner,
            name="Platform-KeyRing",
            defaults={"salt": os.urandom(16)},
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created KeyRing: {keyring.name}"))
        else:
            self.stdout.write(f"Using existing KeyRing: {keyring.name}")

        # -----------------------------
        # SecretKey (symmetric)
        # -----------------------------
        raw_key = secrets.token_urlsafe(secret_size)

        secret = SecretKey(
            keyring=keyring,
            size=secret_size,
        )
        secret.set_key(raw_key, passphrase)
        secret.save()

        self.stdout.write(self.style.SUCCESS(f"Created SecretKey {secret.id}"))

        # -----------------------------
        # RSA Keypair
        # -----------------------------
        if rsa_keypair_id:
            try:
                keypair = RSAKeyPair.objects.get(key_id=rsa_keypair_id)
                self.stdout.write(self.style.SUCCESS(f"Using RSA keypair: {rsa_keypair_id}"))
            except RSAKeyPair.DoesNotExist:
                raise CommandError(f"RSAKeyPair {rsa_keypair_id} not found.")
        else:
            key_id = f"platform-{uuid.uuid4()}"
            self.stdout.write(self.style.NOTICE(f"Generating RSA keypair: {key_id}"))

            keypair = RSAKeyPair.generate(
                key_id=key_id,
                issuer=domain
            )
            keypair.save()

            self.stdout.write(self.style.SUCCESS(f"Created RSA keypair: {keypair.key_id}"))

        # -----------------------------
        # Create or Update Platform
        # -----------------------------
        platform, created = Platform.objects.update_or_create(
            domain=domain,
            defaults={
                "site_name": site_name,
                "publication_year": publication_year,
                "active": active,
                "theme": theme,
                "author": author,
                "api_url": "http://127.0.0.1:8000/core/sync/",
                "api_keypair_in": keypair,
                "api_keypair_out": keypair,
                "api_owner": owner,
                "secret": secret
            },
        )

        # -----------------------------
        # Logo
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
