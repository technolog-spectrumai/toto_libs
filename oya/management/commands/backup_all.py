from django.core.management.base import BaseCommand
from oya.models import AppBackup


class Command(BaseCommand):
    help = "Run backup commands for all AppBackup entries"

    def handle(self, *args, **options):
        total = AppBackup.objects.count()
        success = 0
        failed = 0

        for backup in AppBackup.objects.all():
            try:
                backup.run_backup_command()
                self.stdout.write(self.style.SUCCESS(
                    f"✅ Success: {backup.app_name}.{getattr(backup, 'model_name', '')}"
                ))
                success += 1
            except AppBackup.BackupCommandExecutionFailed as ef:
                self.stdout.write(self.style.ERROR(f"💥 Failed: {ef}"))
                failed += 1
            except AppBackup.BackupCommandError as e:
                self.stdout.write(self.style.WARNING(f"⚠️ Error: {e}"))
                failed += 1

        self.stdout.write("Summary:")
        self.stdout.write(f"Total: {total}")
        self.stdout.write(self.style.SUCCESS(f"✅ Success: {success}"))
        self.stdout.write(self.style.ERROR(f"💥 Failed: {failed}"))
