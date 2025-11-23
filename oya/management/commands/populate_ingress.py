from django.core.management.base import BaseCommand
from django.utils import timezone
from oya.models import AppIngress


class Command(BaseCommand):
    help = "Populate AppIngress entries for all INGRESS_ALLOWED_APPS"

    def handle(self, *args, **options):
        created = []
        skipped = []

        for app_name in AppIngress.INGRESS_ALLOWED_APPS:
            obj, was_created = AppIngress.objects.get_or_create(
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
            self.stdout.write(f"✅ Created AppIngress entries for: {', '.join(created)}")
        if skipped:
            self.stdout.write(f"⚠️ Already existed: {', '.join(skipped)}")
        if not created and not skipped:
            self.stdout.write("ℹ️ No apps found in INGRESS_ALLOWED_APPS")
