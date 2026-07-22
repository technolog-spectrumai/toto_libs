from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
import os
import json
from toto.conf import data_dir
from toto.gervazy.models import EncryptedPrivateKey


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

        FONTS_FILE = str(data_dir(os.path.join(os.path.dirname(__file__), '../../../../../data')) / 'fonts.json')

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
        site_name = os.environ.get("PLATFORM_NAME", "Toto Platform")
        domain = os.environ.get("PLATFORM_DOMAIN", "localhost")
        author = os.environ.get("PLATFORM_AUTHOR", "")

        admin_username = os.environ.get("ADMIN_USERNAME", "admin")
        admin_email = os.environ.get("ADMIN_EMAIL", "")
        admin_first_name = os.environ.get("ADMIN_FIRST_NAME", "")
        admin_last_name = os.environ.get("ADMIN_LAST_NAME", "")

        self.stdout.write(self.style.NOTICE("Creating superuser..."))
        call_command(
            "create_user", admin_username, admin_password,
            admin=True,
            email=admin_email,
            first_name=admin_first_name,
            last_name=admin_last_name,
        )
        self.stdout.write(self.style.SUCCESS("Superuser created."))

        self._create_admin_person(admin_username)

        self.stdout.write(self.style.NOTICE("Creating fonts..."))
        self.create_fonts()
        self.stdout.write(self.style.SUCCESS("Fonts created."))

        self.stdout.write(self.style.NOTICE("Creating fonts and theme..."))
        THEMES_DIR = str(data_dir(os.path.join(os.path.dirname(__file__), '../../../../../data')) / 'themes')
        self.create_theme_from_file(os.path.join(THEMES_DIR, "amazing.json"))
        self.stdout.write(self.style.SUCCESS("Fonts and theme created."))
        theme = self.get_theme("Amazing Moon")
        if theme is None:
            self.stderr.write(self.style.ERROR("Theme not found. Initialization aborted."))
            return

        create_platform_args = [
            site_name,
            domain,
            author,
            "--active=True",
            f"--theme_id={theme.id}",
        ]

        self.stdout.write(self.style.NOTICE("Creating platform..."))
        call_command("create_platform", *create_platform_args)
        self.stdout.write(self.style.SUCCESS("Platform created."))

        self.stdout.write(self.style.NOTICE("Creating federation..."))
        self._create_federation()
        self.stdout.write(self.style.SUCCESS("Federation created."))

    def _create_federation(self) -> None:
        """Always (even at non-full ingress) create the "Toto-Federation", give it the
        okti.png logo, and assign it to the active platform."""
        from django.core.files import File  # noqa: PLC0415
        from toto.core.models import Federation, Platform  # noqa: PLC0415

        federation, created = Federation.objects.get_or_create(
            name="Toto-Federation",
            defaults={"description": ""},
        )
        if created:
            self.stdout.write(self.style.SUCCESS("Created federation: Toto-Federation"))

        logo_path = os.path.abspath(
            str(data_dir(os.path.join(os.path.dirname(__file__), "../../../../../data")) / "img" / "okti.png")
        )
        if not federation.logo and os.path.exists(logo_path):
            with open(logo_path, "rb") as f:
                federation.logo.save("okti.png", File(f), save=True)
            self.stdout.write(self.style.SUCCESS("Federation logo assigned (okti.png)."))
        elif not os.path.exists(logo_path):
            self.stderr.write(self.style.WARNING(f"Federation logo not found at {logo_path}"))

        platform = Platform.objects.filter(active=True).first()
        if platform:
            platform.federation = federation
            platform.save()
            self.stdout.write(
                self.style.SUCCESS(f"Assigned federation to platform '{platform.site_name}'.")
            )

    def _create_admin_person(self, admin_username: str) -> None:
        display_name = os.environ.get("ADMIN_DISPLAY_NAME", "")
        if not display_name:
            return
        try:
            from django.contrib.auth.models import User  # noqa: PLC0415
            from toto.people.models import Person  # noqa: PLC0415
        except ImportError:
            return
        try:
            user = User.objects.get(username=admin_username)
        except User.DoesNotExist:
            return
        person_email = os.environ.get("ADMIN_PERSON_EMAIL", "") or os.environ.get("ADMIN_EMAIL", "")
        person_phone = os.environ.get("ADMIN_PERSON_PHONE", "")
        Person.objects.update_or_create(
            user=user,
            defaults={
                "display_name": display_name,
                **({"email": person_email} if person_email else {}),
                **({"phone": person_phone} if person_phone else {}),
            },
        )
        self.stdout.write(self.style.SUCCESS(f"Person profile created for '{admin_username}'."))

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

