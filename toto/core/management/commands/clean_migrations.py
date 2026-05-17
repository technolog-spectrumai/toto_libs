import os
import glob
import site
from django.core.management.base import BaseCommand
from django.apps import apps


def _is_venv_path(path):
    for s in site.getsitepackages() + [site.getusersitepackages()]:
        if path.startswith(s):
            return True
    return False


class Command(BaseCommand):
    help = "Deletes migration files from project apps only (never touches venv or Django internals)."

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING("⚠️ Deleting migration files..."))

        for app_config in apps.get_app_configs():
            migrations_dir = os.path.join(app_config.path, "migrations")
            if not os.path.isdir(migrations_dir):
                continue

            if _is_venv_path(migrations_dir):
                continue

            migration_files = glob.glob(os.path.join(migrations_dir, "[0-9][0-9][0-9][0-9]_*.py"))
            for file_path in migration_files:
                try:
                    os.remove(file_path)
                    self.stdout.write(self.style.SUCCESS(f"Deleted {file_path}"))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Failed to delete {file_path}: {e}"))

            pyc_files = glob.glob(os.path.join(migrations_dir, "__pycache__", "*.pyc"))
            for file_path in pyc_files:
                try:
                    os.remove(file_path)
                except Exception:
                    pass

        self.stdout.write(self.style.SUCCESS("✅ Migration cleanup complete."))
