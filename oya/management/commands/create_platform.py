from django.core.management.base import BaseCommand
from oya.models import Platform, Theme
from datetime import datetime


class Command(BaseCommand):
    help = "Create or update a Platform instance with company, TLS certificate, optional theme, and index URL"

    def add_arguments(self, parser):
        parser.add_argument('site_name', type=str, help='Platform site name')
        parser.add_argument('domain', type=str, help='Domain name for the platform')
        parser.add_argument('--index_url', type=str, help='Optional index URL for homepage routing')
        parser.add_argument('--active', type=bool, default=True, help='Is the platform active?')
        parser.add_argument('--theme_id', type=int, help='Optional Theme ID for visual configuration')

    def handle(self, *args, **options):
        site_name = options['site_name']
        domain = options['domain']
        index_url = options.get('index_url')
        active = options['active']
        publication_year = datetime.now().year
        theme_id = options.get('theme_id')

        # Create or Update Platform
        platform_data = {
            "publication_year": publication_year,
            "active": active,
        }

        if index_url:
            platform_data["index_url"] = index_url

        # Fetch Theme (optional)
        theme = None
        if theme_id:
            try:
                theme = Theme.objects.get(id=theme_id)
            except Theme.DoesNotExist:
                self.stderr.write(self.style.WARNING(f"Theme with ID {theme_id} not found — continuing without theme."))

        platform, created = Platform.objects.update_or_create(
            domain=domain,
            site_name=site_name,
            defaults=platform_data,
            theme=theme
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created Platform: {platform.site_name}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Updated Platform: {platform.site_name}"))
