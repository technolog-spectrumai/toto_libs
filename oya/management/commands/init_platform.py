from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management import call_command
import os
import django


class Command(BaseCommand):
    help = "Initial setup: clears DB, runs migrations, and accepts admin password and GitHub token"

    def add_arguments(self, parser):
        parser.add_argument(
            '--admin_password',
            default=os.environ.get('ADMIN_PASSWORD', 'admin'),
            type=str,
            help='Password for the admin user'
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

        #self.clear_db()

        self.stdout.write(self.style.NOTICE("Running migrations..."))
        call_command("makemigrations")
        call_command("migrate")

        self.auto_create_migrations()

        call_command("makemigrations")
        call_command("migrate")
        self.stdout.write(self.style.SUCCESS("Migrations completed."))

        admin_password = options['admin_password']
        call_command("init_data", admin_password=admin_password)
        self.stdout.write(self.style.SUCCESS(f"Installation completed."))

    # def clear_db(self):
    #     """Flushes all data from the database without deleting the file"""
    #     db_path = settings.DATABASES.get("default", {}).get("NAME")
    #     if db_path and os.path.exists(db_path):
    #         self.stdout.write(self.style.WARNING("Flushing database..."))
    #         call_command('flush', '--noinput')
    #         self.stdout.write(self.style.SUCCESS("Database flushed successfully."))


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

    # def clear_db(self):
    #     """Flushes all data from the database without deleting the file"""
    #     self.stdout.write(self.style.WARNING("Flushing database..."))
    #     call_command('flush', '--noinput')
    #     self.stdout.write(self.style.SUCCESS("Database flushed successfully."))
