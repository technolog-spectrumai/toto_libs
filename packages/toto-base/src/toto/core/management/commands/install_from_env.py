import os
from pathlib import Path
from dotenv import load_dotenv
from django.core.management.base import BaseCommand
from django.core.management import call_command

class Command(BaseCommand):
    help = "Debug install: loads .env and runs init_platform with env variables"

    def add_arguments(self, parser):
        parser.add_argument(
            '--env_path',
            type=str,
            default='.env',
            help='Path to the .env file'
        )

    def handle(self, *args, **options):
        # Load environment variables from the specified .env file
        env_path = Path(options['env_path']).resolve()
        load_dotenv(dotenv_path=env_path)

        # Fetch values from environment
        admin_password = os.getenv('ADMIN_PASSWORD', 'admin')

        # Call the init_platform command with the loaded values
        call_command('init_platform', admin_password=admin_password)

        self.stdout.write(self.style.SUCCESS("install_debug completed successfully."))
