from django.core.management.base import BaseCommand
from ravioli.models import GraphSync


class Command(BaseCommand):
    help = "Run sync commands for all GraphSync entries"

    def handle(self, *args, **options):
        total = GraphSync.objects.count()
        success = 0
        not_found = 0
        failed = 0

        for sync in GraphSync.objects.all():
            try:
                sync.run_sync_command()
                self.stdout.write(self.style.SUCCESS(f"✅ Success: {sync.app_name}"))
                success += 1
            except GraphSync.SyncCommandNotFound as nf:
                self.stdout.write(self.style.WARNING(f"⚠️ Not Found: {nf}"))
                not_found += 1
            except GraphSync.SyncCommandExecutionFailed as ef:
                self.stdout.write(self.style.ERROR(f"💥 Failed: {ef}"))
                failed += 1
            except GraphSync.SyncCommandError as e:
                self.stdout.write(self.style.NOTICE(f"❓ Unknown Error: {e}"))
                failed += 1

        self.stdout.write("Summary:")
        self.stdout.write(f"Total: {total}")
        self.stdout.write(self.style.SUCCESS(f"✅ Success: {success}"))
        self.stdout.write(self.style.WARNING(f"⚠️ Not Found: {not_found}"))
        self.stdout.write(self.style.ERROR(f"💥 Failed: {failed}"))
