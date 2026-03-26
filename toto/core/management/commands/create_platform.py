import os

from django.conf import settings
from django.core.files import File
from django.core.management import CommandError
from django.core.management.base import BaseCommand
from toto.core.models import Platform, Theme
from toto.gervazy.models import SecretKey   # import SecretKey from gervazy app
from datetime import datetime
import secrets


class Command(BaseCommand):
    help = "Create or update a Platform instance with company, TLS certificate, optional theme, index URL, and secret key"

    def add_arguments(self, parser):
        parser.add_argument('site_name', type=str, help='Platform site name')
        parser.add_argument('domain', type=str, help='Domain name for the platform')
        parser.add_argument('author', type=str, help='Platform Author')
        parser.add_argument('--active', type=bool, default=True, help='Is the platform active?')
        parser.add_argument('--theme_id', type=int, help='Optional Theme ID for visual configuration')
        parser.add_argument('--secret_size', type=int, choices=[64, 128, 256], default=64,
                            help='Size of the secret key (default: 64)')
        parser.add_argument('--passphrase', type=str, default="default-passphrase",
                            help='Passphrase for the secret key')

    def handle(self, *args, **options):
        site_name = options['site_name']
        domain = options['domain']
        active = options['active']
        publication_year = datetime.now().year
        theme_id = options.get('theme_id')
        secret_size = options['secret_size']
        passphrase = options['passphrase']
        author = options['author']

        # Fetch Theme (optional)
        theme = None
        if theme_id:
            try:
                theme = Theme.objects.get(id=theme_id)
            except Theme.DoesNotExist:
                self.stderr.write(self.style.WARNING(
                    f"Theme with ID {theme_id} not found — continuing without theme."
                ))

        secret = SecretKey.objects.create(
            size=secret_size,
            passphrase=passphrase,
            key=secrets.token_urlsafe(secret_size)
        )
        # Create or Update Platform
        platform, created = Platform.objects.update_or_create(
            domain=domain,
            site_name=site_name,
            secret=secret,
            publication_year=publication_year,
            active=active,
            theme = theme,
            author = author
        )

        logo_path = os.path.join(settings.BASE_DIR, settings.PLATFORM_LOGO_FILENAME)
        logo_path = os.path.abspath(logo_path)
        if not os.path.exists(logo_path):
            raise CommandError(f"Logo file not found at {logo_path}")

        if not platform.logo:
            with open(logo_path, "rb") as f:
                platform.logo.save("platform_logo.png", File(f), save=True)
        platform.save()

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created Platform: {platform.site_name}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Updated Platform: {platform.site_name}"))
