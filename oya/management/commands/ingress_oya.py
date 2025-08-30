import os
import json
from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command


class Command(BaseCommand):
    help = "Ingress themes from a hardcoded directory of JSON files"

    # 🔒 Hardcoded path to your themes directory
    THEMES_DIR = os.path.join(os.path.dirname(__file__), '../../../../data/themes')

    def handle(self, *args, **options):
        if not os.path.isdir(self.THEMES_DIR):
            raise CommandError(f"Hardcoded path is not a directory: {self.THEMES_DIR}")

        theme_files = [
            f for f in os.listdir(self.THEMES_DIR)
            if f.endswith('.json') and os.path.isfile(os.path.join(self.THEMES_DIR, f))
        ]

        if not theme_files:
            raise CommandError("No JSON files found in the hardcoded directory.")

        for filename in theme_files:
            file_path = os.path.join(self.THEMES_DIR, filename)
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

        dashboard_blocks = [
            {
                "name": "Kanban",
                "description": "Organize tasks and workflows visually.",
                "icon": "fas fa-columns",
                "link": "/kanban"
            },
            {
                "name": "Documents",
                "description": "Access and manage your documents.",
                "icon": "fas fa-file-alt",
                "link": "/documents"
            }
        ]

        for block in dashboard_blocks:
            call_command(
                "create_dashboard_block",
                block["name"],
                f"--description={block['description']}",
                f"--icon={block['icon']}",
                f"--link={block['link']}"
            )

        self.stdout.write(self.style.SUCCESS("Themes and dashboard blocks created successfully."))
