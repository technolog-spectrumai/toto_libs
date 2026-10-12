from django.conf import settings
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

    def run(self, admin_password):
        site_name = os.environ.get("PLATFORM_NAME", "Toto Platform")
        domain = os.environ.get("PLATFORM_DOMAIN", "localhost")
        author = os.environ.get("PLATFORM_AUTHOR", "")

        admin_username = os.environ.get("ADMIN_USERNAME", "admin")
        admin_email = os.environ.get("ADMIN_EMAIL", "")
        admin_first_name = os.environ.get("ADMIN_FIRST_NAME", "")
        admin_last_name = os.environ.get("ADMIN_LAST_NAME", "")

        self.stdout.write(self.style.NOTICE("Creating superuser..."))
        apply_password = self._configured_password_is_new(admin_username, admin_password)
        call_command(
            "create_user", admin_username, admin_password,
            admin=True,
            email=admin_email,
            first_name=admin_first_name,
            last_name=admin_last_name,
            keep_password=not apply_password,
        )
        if apply_password:
            self._remember_configured_password(admin_username, admin_password)
        else:
            self.stdout.write("Admin password unchanged since the last start: kept.")
        self.stdout.write(self.style.SUCCESS("Superuser created."))

        self._create_admin_person(admin_username)
        # The data-mesh read gate's group: api's migration seeds it, and so
        # does this, so a database that lost it gets it back.
        from toto.api.cors import ensure_data_mesh_group

        ensure_data_mesh_group()
        self.stdout.write(self.style.SUCCESS("data_mesh group present."))
        # The admin is a superuser with the admin panel; on a host that sells
        # plans it must also HOLD the Superuser plan (every superuser may, no
        # Community offer needed since 2026-09-28) — bootstrap_plans makes the
        # operators' Community, offers every plan to it and subscribes every
        # superuser.
        from django.apps import apps as django_apps

        if django_apps.is_installed("toto.subscriptions"):
            call_command("bootstrap_plans", stdout=self.stdout)

        self.stdout.write(self.style.NOTICE("Creating fonts..."))
        self.create_fonts()
        self.stdout.write(self.style.SUCCESS("Fonts created."))

        # The colours are one file in toto-base (toto/core/palette.json),
        # compiled into the stylesheet when the image is built: nothing of
        # them is seeded. A palette that does not read stops here, in words,
        # before any platform: every page would fail on it.
        from toto.core import palette  # noqa: PLC0415

        try:
            font_name = palette.font_name()
        except palette.PaletteError as exc:
            self.stderr.write(self.style.ERROR(f"Palette unreadable: {exc}. Initialization aborted."))
            return

        create_platform_args = [
            site_name,
            domain,
            author,
            "--active=True",
            f"--font={font_name}",
        ]

        self.stdout.write(self.style.NOTICE("Creating platform..."))
        call_command("create_platform", *create_platform_args,
                     stdout=self.stdout, stderr=self.stderr)
        self.stdout.write(self.style.SUCCESS("Platform created."))

        if getattr(settings, "FEDERATION_ENABLED", True):
            self.stdout.write(self.style.NOTICE("Creating federation..."))
            self._create_federation()
            self.stdout.write(self.style.SUCCESS("Federation created."))
        else:
            from toto.core.models import Platform  # noqa: PLC0415

            Platform.objects.filter(active=True).update(federation=None)
            self.stdout.write(self.style.SUCCESS("Federation disabled for this host."))

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

    # Stage 51: every start used to set ADMIN_PASSWORD again, so a password
    # changed on the profile (after a leak, say) was silently undone by the
    # next restart. Now the configured password is applied when the account
    # is new, when it differs from the one applied at the last start (a new
    # ADMIN_PASSWORD, deploy.py --admin-password), or when no start has
    # recorded one yet (an admin from before this, admin/admin included,
    # converges once). What is recorded is a salted hash, never the password.

    @staticmethod
    def _marker_name(username: str) -> str:
        return f"admin-password:{username}"

    def _configured_password_is_new(self, username: str, password: str) -> bool:
        from django.contrib.auth import get_user_model  # noqa: PLC0415
        from django.contrib.auth.hashers import check_password  # noqa: PLC0415
        from toto.core.models import BootstrapMarker  # noqa: PLC0415

        if not get_user_model().objects.filter(username=username).exists():
            return True
        marker = BootstrapMarker.objects.filter(name=self._marker_name(username)).first()
        if marker is None or not marker.value:
            return True
        return not check_password(password, marker.value)

    def _remember_configured_password(self, username: str, password: str) -> None:
        from django.contrib.auth.hashers import make_password  # noqa: PLC0415
        from toto.core.models import BootstrapMarker  # noqa: PLC0415

        BootstrapMarker.objects.update_or_create(
            name=self._marker_name(username), defaults={"value": make_password(password)})

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

    def get_font(self, name):
        """Fetches a Font object by name"""
        from toto.core.models import Font  # Local import to avoid circular imports
        try:
            return Font.objects.get(name=name)
        except Font.DoesNotExist:
            return None
