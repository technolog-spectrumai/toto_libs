import os
from datetime import datetime

from django.core.files import File
from django.core.management import CommandError
from django.core.management.base import BaseCommand
from django.conf import settings
from django.contrib.auth.models import User

from toto.conf import data_dir
from toto.core.models import Platform, Theme


class Command(BaseCommand):
    help = "Create or update a Platform with theme, logo, and metadata"

    def add_arguments(self, parser):
        parser.add_argument("site_name", type=str)
        parser.add_argument("domain", type=str)
        parser.add_argument("author", type=str)
        parser.add_argument("--active", type=bool, default=True)
        parser.add_argument("--theme_id", type=int)

    def handle(self, *args, **options):
        site_name = options["site_name"]
        domain = options["domain"]
        author = options["author"]
        active = options["active"]
        theme_id = options.get("theme_id")
        publication_year = datetime.now().year

        theme = None
        if theme_id:
            try:
                theme = Theme.objects.get(id=theme_id)
            except Theme.DoesNotExist:
                self.stderr.write(self.style.WARNING(f"Theme {theme_id} not found."))

        admin_username = os.environ.get("ADMIN_USERNAME", "admin")
        try:
            owner = User.objects.get(username=admin_username)
        except User.DoesNotExist:
            raise CommandError(f"Admin user '{admin_username}' not found.")

        platform, created = Platform.objects.update_or_create(
            domain=domain,
            defaults={
                "site_name": site_name,
                "publication_year": publication_year,
                "active": active,
                "theme": theme,
                "author": author,
                "api_url": "http://127.0.0.1:8000/core/sync/",
                "api_owner": owner,
            },
        )

        logo_path = os.path.abspath(
            os.path.join(settings.BASE_DIR, settings.PLATFORM_LOGO_PATH)
        )
        if not os.path.exists(logo_path):
            # toto core default — okti.png, the federation mascot — when the deployment
            # doesn't set a PLATFORM_LOGO_PATH (resolved relative to this command, not
            # BASE_DIR; TOTO_DATA_DIR makes that the host's own data/ dir).
            logo_path = os.path.abspath(
                str(data_dir(os.path.join(os.path.dirname(__file__), "../../../../../data")) / "img" / "okti.png")
            )

        if os.path.exists(logo_path):
            # (Re)assign whenever the configured logo differs, so a redeploy with a new
            # logo takes effect; idempotent otherwise. Note this compares the filename
            # stem only — replacing the *bytes* under an unchanged name is not picked up.
            desired_stem = os.path.splitext(os.path.basename(logo_path))[0]
            current_name = os.path.basename(platform.logo.name) if platform.logo else ""
            if not current_name.startswith(desired_stem):
                with open(logo_path, "rb") as f:
                    platform.logo.save(os.path.basename(logo_path), File(f), save=True)
        else:
            # Warn, never raise: this command runs inside init_data, whose handler
            # swallows CommandError — so failing here used to abort the whole seed
            # before the federation was created, losing far more than a logo.
            self.stderr.write(self.style.WARNING(
                f"Platform logo not found at {logo_path} — leaving the platform "
                "logo unset (set PLATFORM_LOGO_PATH to a file that exists)."
            ))

        platform.save()

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created Platform: {platform.site_name}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Updated Platform: {platform.site_name}"))
