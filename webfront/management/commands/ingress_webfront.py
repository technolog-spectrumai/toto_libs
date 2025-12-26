import os
import json
from django.conf import settings
from django.core.management.base import BaseCommand
from webfront.models import DynamicPage, PageWidget


class Command(BaseCommand):
    help = "Sync Dynamic Pages and Page Widgets from filesystem"

    def handle(self, *args, **options):
        # Example: project_root/../data/webfront
        base_path = os.path.abspath(
            os.path.join(settings.BASE_DIR, "..", "data", "webfront")
        )

        pages_path = os.path.join(base_path, "pages")
        lambdas_path = os.path.join(base_path, "lambdas")

        if not os.path.isdir(pages_path):
            self.stdout.write(self.style.WARNING(f"No pages directory found at {pages_path}"))
            return

        self.stdout.write(self.style.MIGRATE_HEADING("Syncing Dynamic Pages…"))

        for fname in os.listdir(pages_path):
            if not fname.endswith(".json"):
                continue

            full_path = os.path.join(pages_path, fname)
            with open(full_path, "r") as f:
                data = json.load(f)

            # Create or update page
            page, created = DynamicPage.objects.update_or_create(
                name=data["name"],
                defaults={
                    "description": data.get("description", ""),
                    # slug auto-generated in model save()
                },
            )

            self.stdout.write(f"  - Page: {page.name} ({'created' if created else 'updated'})")

            # Sync widgets
            self._sync_widgets(page, data.get("widgets", []), lambdas_path)

        self.stdout.write(self.style.SUCCESS("Dynamic Page ingress complete"))

    #
    # Load lambda file from lambdas directory
    #
    def _load_lambda(self, lambdas_root, rel_path):
        if not rel_path:
            return None

        full_path = os.path.join(lambdas_root, rel_path)
        if not os.path.isfile(full_path):
            return None

        with open(full_path, "r") as f:
            return f.read()

    #
    # Sync widgets for a page
    #
    def _sync_widgets(self, page, widgets_data, lambdas_path):
        existing_ids = []

        for w in widgets_data:
            code = self._load_lambda(lambdas_path, w.get("lambda"))

            widget, created = PageWidget.objects.update_or_create(
                page=page,
                name=w["name"],  # unique per page
                defaults={
                    "widget_type": w["widget_type"],
                    "config": w.get("config", {}),
                    "code": code,
                    "test_context": w.get("test_context"),
                },
            )

            existing_ids.append(widget.id)

        # Remove widgets not present in filesystem
        PageWidget.objects.filter(page=page).exclude(id__in=existing_ids).delete()
