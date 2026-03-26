import os
import json

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
from toto.core.ingress import IngressCommand
from toto.core.models import Platform, Federation
from django.conf import settings


class Command(IngressCommand):
    help = "Ingress themes from a directory of JSON files"


    def create_federation(self):
        """
        Creates a federation if missing, assigns logo if available, and returns it.
        """

        federation_name = getattr(self, "federation_name", "Default Federation")
        federation_description = getattr(self, "federation_description", "")

        federation, created = Federation.objects.get_or_create(
            name=federation_name,
            defaults={"description": federation_description}
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created federation: {federation.name}"))
        else:
            self.stdout.write(self.style.NOTICE(f"Federation already exists: {federation.name}"))

        # --- Logo ingestion ---
        logo_path = os.path.join(settings.BASE_DIR, "..", "data", "img", "federation_logo.png")
        logo_path = os.path.abspath(logo_path)

        if not os.path.exists(logo_path):
            self.stderr.write(self.style.WARNING(f"No federation logo found at {logo_path}"))
            return federation

        if not federation.logo:
            with open(logo_path, "rb") as f:
                federation.logo.save("federation_logo.png", File(f), save=True)
            self.stdout.write(self.style.SUCCESS("Federation logo assigned."))
        else:
            self.stdout.write(self.style.NOTICE("Federation already has a logo."))

        return federation


    def assign_federation_to_platform(self, federation):
        """
        Assigns the given federation to the active platform.
        """

        platform = Platform.objects.filter(active=True).first()
        if not platform:
            raise CommandError("No active platform found.")

        platform.federation = federation
        platform.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"Assigned federation '{federation.name}' to platform '{platform.site_name}'"
            )
        )

    def process(self):
        if not self.full:
            return
        themes_dir = os.path.join(os.path.dirname(__file__), '../../../../../data/themes')

        if not os.path.isdir(themes_dir):
            raise CommandError(f"Provided path is not a directory: {themes_dir}")

        theme_files = [
            f for f in os.listdir(themes_dir)
            if f.endswith('.json') and os.path.isfile(os.path.join(themes_dir, f))
        ]

        federation = self.create_federation()
        self.assign_federation_to_platform(federation)

        if not theme_files:
            raise CommandError("No JSON files found in the specified directory.")

        for filename in theme_files:
            file_path = os.path.join(themes_dir, filename)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    theme = json.load(f)
            except json.JSONDecodeError as e:
                self.stderr.write(self.style.ERROR(f"Invalid JSON in {filename}: {e}"))
                continue

            if not all(k in theme for k in ['name', 'font', 'colors']):
                self.stderr.write(self.style.ERROR(f"Missing required keys in {filename}"))
                continue

            self.stdout.write(self.style.NOTICE(f"Creating theme from {filename}: {theme['name']}"))
            call_command(
                "create_theme",
                "--name", theme["name"],
                "--font", theme["font"],
                "--colors", json.dumps(theme["colors"]),
                "--header", json.dumps(theme.get("header", {}))
            )
        self.stdout.write(self.style.SUCCESS("Themes created successfully."))