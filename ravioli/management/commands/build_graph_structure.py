from django.core.management.base import BaseCommand
from django.conf import settings
from django.apps import apps

from ravioli.collector import CollectorHelper
from ravioli.models import Collector  # or your Collector class


class Command(BaseCommand):
    help = "Build CollectionTypes and RelationTypes for all GRAPH_ALLOWED_APPS."

    def handle(self, *args, **options):
        allowed_apps = getattr(settings, "GRAPH_ALLOWED_APPS", [])

        if not allowed_apps:
            self.stdout.write(self.style.ERROR("GRAPH_ALLOWED_APPS is empty."))
            return

        self.stdout.write(self.style.WARNING("Building graph types..."))

        for app_label in allowed_apps:
            try:
                # Ensure app exists
                apps.get_app_config(app_label)
            except LookupError:
                self.stdout.write(self.style.ERROR(f"❌ App not found: {app_label}"))
                continue

            # Create a Collector instance for this app
            collector = Collector(app_name=app_label)

            # Run type builder
            try:
                CollectorHelper.build_types(collector)
                self.stdout.write(self.style.SUCCESS(f"✔ Created types for {app_label}"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"❌ Error in {app_label}: {e}"))

        self.stdout.write(self.style.SUCCESS("🎉 Graph type building complete."))
