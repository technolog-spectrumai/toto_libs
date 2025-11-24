from django.core.management.base import BaseCommand
from django.utils import timezone
from ravioli.models import GraphSync


class Command(BaseCommand):
    help = "Populate GraphSync entries for all GRAPH_ALLOWED_APPS"

    def handle(self, *args, **options):
        created = []
        skipped = []

        for app_name in GraphSync.GRAPH_ALLOWED_APPS:
            obj, was_created = GraphSync.objects.get_or_create(
                app_name=app_name,
                defaults={
                    "scheduled_at": timezone.now()
                }
            )
            if was_created:
                created.append(app_name)
            else:
                skipped.append(app_name)

        if created:
            self.stdout.write(f"✅ Created GraphSync entries for: {', '.join(created)}")
        if skipped:
            self.stdout.write(f"⚠️ Already existed: {', '.join(skipped)}")
        if not created and not skipped:
            self.stdout.write("ℹ️ No apps found in GRAPH_ALLOWED_APPS")
