from django.core.management.base import BaseCommand
from django.conf import settings
from pathlib import Path
from audit.models import AuditLog


class Command(BaseCommand):
    help = 'Sync AuditLog entries with dynamically configured log files'

    def handle(self, *args, **kwargs):
        log_dir = Path(settings.BASE_DIR) / 'logs'
        log_dir.mkdir(exist_ok=True)

        installed_loggers = {}
        for app in settings.AUDITED_APPS:
            app_label = app.split('.')[-1]
            log_file = log_dir / f"{app_label}.log"
            installed_loggers[app_label] = log_file

        created, skipped = 0, 0
        for app_name, log_path in installed_loggers.items():
            obj, was_created = AuditLog.objects.get_or_create(
                appname=app_name,
                defaults={'filepath': str(log_path)}
            )
            if was_created:
                created += 1
                self.stdout.write(self.style.SUCCESS(f"Created AuditLog for {app_name}"))
            else:
                skipped += 1
                self.stdout.write(f"Skipped {app_name} (already exists)")

        self.stdout.write(self.style.NOTICE(f"\nDone. {created} created, {skipped} skipped."))
