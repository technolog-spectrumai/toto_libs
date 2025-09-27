from django.core.management.base import BaseCommand
from oya.models import AppIngress
from django.utils import timezone

class Command(BaseCommand):
    help = "Run ingress commands for all allowed apps"

    def handle(self, *args, **options):
        now = timezone.now()
        ingress_entries = AppIngress.objects.filter(scheduled_at__lte=now)

        if not ingress_entries.exists():
            self.stdout.write(self.style.WARNING("No scheduled ingress entries found."))
            return

        for ingress in ingress_entries:
            self.stdout.write(f"Running ingress for {ingress.app_name}...")
            try:
                ingress.run_ingress_command()
                self.stdout.write(self.style.SUCCESS(f"Successfully ran ingress for {ingress.app_name}"))
            except AppIngress.IngressCommandError as e:
                self.stdout.write(self.style.ERROR(f"Failed to run ingress for {ingress.app_name}: {str(e)}"))
