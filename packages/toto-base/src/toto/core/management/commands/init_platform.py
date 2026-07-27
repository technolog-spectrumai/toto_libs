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
        call_command("migrate")
        self.stdout.write(self.style.SUCCESS("Migrations complete."))

        admin_password = options['password']
        call_command("init_data", password=admin_password)
        self.stdout.write(self.style.SUCCESS("Installation completed."))

    def clear_db(self):
        """Reset the database: delete SQLite file or drop+recreate PostgreSQL schema."""
        from django.db import connection  # noqa: PLC0415

        db_config = settings.DATABASES.get("default", {})
        db_engine = db_config.get("ENGINE", "")

        if "sqlite3" in db_engine or "spatialite" in db_engine:
            db_path = db_config.get("NAME")
            if db_path and os.path.exists(db_path):
                self.stdout.write(self.style.WARNING(f"Deleting SQLite database: {db_path}"))
                os.remove(db_path)
                self.stdout.write(self.style.SUCCESS("SQLite database deleted."))
        else:
            self.stdout.write(self.style.WARNING("Resetting PostgreSQL schema..."))
            with connection.cursor() as cursor:
                cursor.execute("DROP SCHEMA public CASCADE;")
                cursor.execute("CREATE SCHEMA public;")
                cursor.execute("GRANT ALL ON SCHEMA public TO public;")
            self.stdout.write(self.style.SUCCESS("PostgreSQL schema reset."))


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
