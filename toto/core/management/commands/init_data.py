from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
import os
import json


class Command(BaseCommand):
    help = "Initialize platform"

    def add_arguments(self, parser):
        parser.add_argument(
            '--password',
            type=str,
            help='Password for the admin user',
            default='admin'
        )

    def handle(self, *args, **options):
        try:
            admin_password = options.get('password', 'admin')
            self.run(admin_password)
        except CommandError as e:
            self.stderr.write(self.style.ERROR(f"Error initializing platform: {e}"))

    def create_fonts(self):
        from toto.core.models import Font

        FONTS_FILE = os.path.join(os.path.dirname(__file__), '../../../../../data/fonts.json')

        if not os.path.isfile(FONTS_FILE):
            self.stderr.write(self.style.ERROR(f"Fonts file not found: {FONTS_FILE}"))
            return

        try:
            with open(FONTS_FILE, 'r', encoding='utf-8') as f:
                font_data = json.load(f)
        except json.JSONDecodeError as e:
            self.stderr.write(self.style.ERROR(f"Invalid JSON in fonts file: {e}"))
            return

        for name, cdn in font_data.items():
            self.stdout.write(self.style.NOTICE(f"Creating font: {name}"))

            font, created = Font.objects.get_or_create(
                name=name,
                defaults={"cdn_link": cdn}
            )

            if created:
                self.stdout.write(self.style.SUCCESS(f"Created Font: {name}"))
            else:
                self.stdout.write(self.style.WARNING(f"Font already exists: {name}"))

    def create_theme_from_file(self, file_path):
        if not os.path.isfile(file_path):
            if self.stderr:
                self.stderr.write(f"Theme file not found: {file_path}")
            return

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                theme = json.load(f)
        except json.JSONDecodeError as e:
            if self.stderr:
                self.stderr.write(f"Invalid JSON in theme file: {e}")
            return

        if not all(k in theme for k in ['name', 'font', 'colors']):
            if self.stderr:
                self.stderr.write("Missing required keys in theme file")
            return

        if self.stdout:
            self.stdout.write(f"Creating theme: {theme['name']}")

        call_command(
            "create_theme",
            "--name", theme["name"],
            "--font", theme["font"],
            "--colors", json.dumps(theme["colors"]),
            "--header", json.dumps(theme.get("header", {}))
        )

    def run(self, admin_password):
        # with Neo4jHelper() as neo4j:
        #     neo4j.flush_db_batch()
        #     self.stdout.write(self.style.SUCCESS("Neo4j cleaned."))
        domain = "spectrumai.pl"

        self.stdout.write(self.style.NOTICE("Creating superuser..."))
        call_command("create_user", "admin", admin_password, admin=True)
        self.stdout.write(self.style.SUCCESS("Superuser created."))

        self.stdout.write(self.style.NOTICE("Creating fonts..."))
        self.create_fonts()
        self.stdout.write(self.style.SUCCESS("Fonts created."))

        site_name = "Spectrum Platform"
        self.stdout.write(self.style.NOTICE("Creating platform..."))

        self.stdout.write(self.style.NOTICE("Creating fonts and theme..."))
        THEMES_DIR = os.path.join(os.path.dirname(__file__), '../../../../../data/themes')
        self.create_theme_from_file(os.path.join(THEMES_DIR, "spectre.json"))
        self.stdout.write(self.style.SUCCESS("Fonts and theme created."))
        theme = self.get_theme("Elegant Spectrum")
        if theme is None:
            self.stderr.write(self.style.ERROR("Theme not found. Initialization aborted."))
            return
        author = "www.spectrumAi.pl"
        create_platform_args = [
            site_name,
            domain,
            author,
            "--active=True",
            f"--theme_id={theme.id}",  # pass theme id
            "--secret_size=64",  # let create_platform handle SecretKey
            "--passphrase=qwerty"
        ]

        self.stdout.write(self.style.NOTICE("Creating platform..."))
        call_command("create_platform", *create_platform_args)
        self.stdout.write(self.style.SUCCESS("Platform created."))
        #self.stdout.write(self.style.NOTICE("Cleaning Neo4j..."))

    def get_theme(self, name):
        """Fetches the latest Theme ID to be used in platform creation"""
        from toto.core.models import Theme  # Import locally to avoid circular imports
        try:
            return Theme.objects.get(name=name)
        except Theme.DoesNotExist:
            return None

    def get_font(self, name):
        """Fetches a Font object by name"""
        from toto.core.models import Font  # Local import to avoid circular imports
        try:
            return Font.objects.get(name=name)
        except Font.DoesNotExist:
            return None


