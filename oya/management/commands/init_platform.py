import os
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
import os
import django

class Command(BaseCommand):
    help = "Initialize platform"

    def handle(self, *args, **options):
        try:
            self.run()
        except CommandError as e:
            self.stderr.write(self.style.ERROR(f"Error initializing platform: {e}"))

    def auto_create_migrations(self):
        # Set up Django environment
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'your_project_name.settings')
        django.setup()

        # Loop through installed apps and run makemigrations
        for app in settings.INSTALLED_APPS:
            try:
                if app.find('django.contrib') != -1:
                    if app.find('auth') != -1:
                        call_command('makemigrations', 'auth')
                    else:
                        continue
                else:
                    call_command('makemigrations', app)
            except Exception as e:
                print(f"Skipping {app}: {e}")

    def create_fonts(self):
        font_data = {
            "Roboto": "https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap",
            "Playfair Display": "https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700&display=swap",
            "Orbitron": "https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&display=swap"
        }

        for name, cdn in font_data.items():
            self.stdout.write(self.style.NOTICE(f"Creating font: {name}"))
            call_command("create_font", "--name", name, "--cdn", cdn)

    def create_themes(self):
        import json
        themes = [
            {
                "name": "ElegantSpectrum",
                "font": "Roboto",
                "colors": {
                    "primary-bg-light": "#ffffff",
                    "header-bg-light": "#efefef",
                    "appbar-bg-light": "#dbdbdb",
                    "bubble-bg-light": "#f8f8f8",
                    "text-main-light": "#000000",
                    "primary-bg-dark": "#000000",
                    "header-bg-dark": "#1a1a1a",
                    "appbar-bg-dark": "#2f2f2f",
                    "bubble-bg-dark": "#0a0a0a",
                    "text-main-dark": "#ffffff",
                    "accent-light": "#a00020",
                    "accent-dark": "#ff3b5c",
                    "warn-light": "#ff8800",
                    "warn-dark": "#fff0cc",
                    "accent-1": "#444c56",
                    "accent-2": "#c8d1db"
                }
            }
        ]

        for theme in themes:
            self.stdout.write(self.style.NOTICE(f"Creating theme: {theme['name']}"))
            call_command(
                "create_theme",
                "--name", theme["name"],
                "--font", theme["font"],
                "--colors", json.dumps(theme["colors"]),
                "--header", json.dumps(theme.get("header", {}))
            )

    def run(self):
        self.clear_db()

        self.stdout.write(self.style.NOTICE("Running migrations..."))
        call_command("makemigrations")
        call_command("migrate")
        self.auto_create_migrations()
        call_command("makemigrations")
        call_command("migrate")
        self.stdout.write(self.style.SUCCESS("Migrations completed."))

        domain = "spectrumai.pl"

        self.stdout.write(self.style.NOTICE("Creating superuser..."))
        call_command("create_user", "admin", "admin", admin=True)
        self.stdout.write(self.style.SUCCESS("Superuser created."))

        self.stdout.write(self.style.NOTICE("Creating fonts..."))
        self.create_fonts()
        self.stdout.write(self.style.SUCCESS("Fonts created."))

        site_name = "TOTO Community Platform"
        self.stdout.write(self.style.NOTICE("Creating platform..."))

        create_platform_args = [
            site_name,
            domain,
            "--active=True"
        ]

        self.stdout.write(self.style.NOTICE("Creating fonts and theme..."))
        self.create_themes()
        self.stdout.write(self.style.SUCCESS("Fonts and theme created."))
        theme = self.get_theme("ElegantSpectrum")
        if theme is None:
            self.stderr.write(self.style.ERROR("Theme not found. Initialization aborted."))
            return
        create_platform_args.append(f"--theme={theme.id}")

        self.stdout.write(self.style.NOTICE("Creating platform..."))
        call_command("create_platform", *create_platform_args)
        self.stdout.write(self.style.SUCCESS("Platform created."))

        call_command("create_platform", *create_platform_args)
        self.stdout.write(self.style.SUCCESS("Platform created."))

        self.stdout.write(self.style.NOTICE("Creating default dashboard blocks..."))
        call_command("create_dashboard_block", "Forum",
                     "--description=Engage in discussions with the community.",
                     "--icon=fas fa-comments", "--link=/nest/not-implemented/")
        call_command("create_dashboard_block", "Profile",
                     "--description=Manage your personal information and settings.",
                     "--icon=fas fa-user", "--link=/nest/not-implemented/")
        self.stdout.write(self.style.SUCCESS("Dashboard blocks created successfully."))

    def get_theme(self, name):
        """Fetches the latest Theme ID to be used in platform creation"""
        from oya.models import Theme  # Import locally to avoid circular imports
        try:
            return Theme.objects.get(name=name)
        except Theme.DoesNotExist:
            return None

    def get_font(self, name):
        """Fetches a Font object by name"""
        from oya.models import Font  # Local import to avoid circular imports
        try:
            return Font.objects.get(name=name)
        except Font.DoesNotExist:
            return None

    def clear_db(self):
        """Removes the existing database file if present"""
        db_path = settings.DATABASES.get("default", {}).get("NAME")
        if db_path and os.path.exists(db_path):
            self.stdout.write(self.style.WARNING("Removing existing database..."))
            os.remove(db_path)
            self.stdout.write(self.style.SUCCESS("Database removed successfully."))


