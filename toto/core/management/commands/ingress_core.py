import os
import json

from django.core.management.base import CommandError
from django.core.management import call_command
from toto.conf import data_dir
from toto.ingress import IngressCommand


class Command(IngressCommand):
    help = "Ingress themes from a directory of JSON files"


    def process(self):
        # The federation ("Toto-Federation" + okti.png) is created unconditionally by
        # init_data (so it exists even without FULL_INGRESS). ingress_core only adds the
        # extra demo themes, which stay full-ingress only.
        if not self.full:
            return
        themes_dir = str(data_dir(os.path.join(os.path.dirname(__file__), '../../../../../data')) / 'themes')

        if not os.path.isdir(themes_dir):
            raise CommandError(f"Provided path is not a directory: {themes_dir}")

        theme_files = [
            f for f in os.listdir(themes_dir)
            if f.endswith('.json') and os.path.isfile(os.path.join(themes_dir, f))
        ]

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