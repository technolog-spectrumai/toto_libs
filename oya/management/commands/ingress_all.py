from django.core.management.base import BaseCommand
from oya.models import AppIngress

class Command(BaseCommand):
    help = "Run ingress commands for all AppIngress entries"

    def handle(self, *args, **options):
        total = AppIngress.objects.count()
        success = 0
        not_found = 0
        failed = 0

        for ingress in AppIngress.objects.all():
            try:
                ingress.run_ingress_command()
                self.stdout.write(self.style.SUCCESS(f"✅ Success: {ingress.app_name}"))
                success += 1
            except AppIngress.IngressCommandNotFound as nf:
                self.stdout.write(self.style.WARNING(f"⚠️ Not Found: {nf}"))
                not_found += 1
            except AppIngress.IngressCommandExecutionFailed as ef:
                self.stdout.write(self.style.ERROR(f"💥 Failed: {ef}"))
                failed += 1
            except AppIngress.IngressCommandError as e:
                self.stdout.write(self.style.NOTICE(f"❓ Unknown Error: {e}"))
                failed += 1

        self.stdout.write("Summary:")
        self.stdout.write(f"Total: {total}")
        self.stdout.write(self.style.SUCCESS(f"✅ Success: {success}"))
        self.stdout.write(self.style.WARNING(f"⚠️ Not Found: {not_found}"))
        self.stdout.write(self.style.ERROR(f"💥 Failed: {failed}"))
