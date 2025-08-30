from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management import call_command
import os
import django


class Command(BaseCommand):
    help = "Initial setup: clears DB, runs migrations, and accepts admin password and GitHub token"

    def add_arguments(self, parser):
        parser.add_argument(
            '--password',
            default=os.environ.get('ADMIN_PASSWORD', 'admin'),
            type=str,
            help='Password for the admin user'
        )
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Flush the database before running migrations'
        )

    def auto_create_migrations(self):
        # Set up Django environment
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'your_project_name.settings')
        django.setup()

        # Loop through installed apps and run makemigrations
        for app in settings.INSTALLED_APPS:
            try:
                if app.find('django.contrib') != -1:
                    if app.find('auth') != -1:
                        call_command('makemigrations', 'auth')
                    else:
                        continue
                else:
                    call_command('makemigrations', app)
            except Exception as e:
                print(f"Skipping {app}: {e}")

    def handle(self, *args, **options):

        if options.get('reset'):
            self.clear_db()

        self.stdout.write(self.style.NOTICE("Running migrations..."))
        call_command("makemigrations")
        call_command("migrate")

        self.auto_create_migrations()

        call_command("makemigrations")
        call_command("migrate")
        self.stdout.write(self.style.SUCCESS("Migrations completed."))

        admin_password = options['password']
        call_command("init_data", password=admin_password)
        self.stdout.write(self.style.SUCCESS(f"Installation completed."))

    def clear_db(self):
        """Deletes the SQLite database file if it exists, otherwise flushes the DB"""
        db_config = settings.DATABASES.get("default", {})
        db_path = db_config.get("NAME")
        db_engine = db_config.get("ENGINE", "")

        if db_engine == "django.db.backends.sqlite3" and db_path and os.path.exists(db_path):
            self.stdout.write(self.style.WARNING(f"Deleting SQLite database file: {db_path}"))
            os.remove(db_path)
            self.stdout.write(self.style.SUCCESS("SQLite database file deleted successfully."))
        else:
            self.stdout.write(self.style.WARNING("Flushing database..."))
            call_command('flush', '--noinput')
            self.stdout.write(self.style.SUCCESS("Database flushed successfully."))

    def auto_create_migrations(self):
        # Set up Django environment
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'your_project_name.settings')
        django.setup()

        # Loop through installed apps and run makemigrations
        for app in settings.INSTALLED_APPS:
            try:
                if app.find('django.contrib') != -1:
                    if app.find('auth') != -1:
                        call_command('makemigrations', 'auth')
                    else:
                        continue
                else:
                    call_command('makemigrations', app)
            except Exception as e:
                print(f"Skipping {app}: {e}")
