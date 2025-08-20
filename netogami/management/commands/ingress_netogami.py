from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
import json
import os

class Command(BaseCommand):
    help = 'Ingress command to create repo and hardcoded pages from a JSON directory'

    def add_arguments(self, parser):
        parser.add_argument('--repo_url', type=str, default="https://github.com/technolog-spectrumai/websites", help='Git repository URL')
        parser.add_argument('--access_token', type=str, default="???", help='Access token for the repository')
        parser.add_argument('--repo_name', type=str, help='Optional name for the repository')
        parser.add_argument('--branch', type=str, default='main', help='Branch to use')
        parser.add_argument('--username', type=str, default='admin', help='Author username')
        parser.add_argument('--json_dir', type=str, default="../data", help='Directory containing JSON config files')

    def handle(self, *args, **options):
        repo_url = options['repo_url']
        token = options['access_token']
        repo_name = options.get('repo_name') or repo_url.split('/')[-1].replace('.git', '')
        branch = options['branch']
        username = options['username']
        json_dir = options['json_dir']

        if not os.path.isdir(json_dir):
            raise CommandError(f"Provided path is not a directory: {json_dir}")

        # Hardcoded page definitions
        pages = [
            { "name": "spectrumai.html", "template": "spectrumai", "json_file": "spectrumai.json" },
            { "name": "basilisk.html", "template": "basilisk", "json_file": "basilisk.json" }
        ]

        try:
            # Step 1: Create repository
            self.stdout.write(self.style.NOTICE("Creating repository..."))
            call_command('create_repo', repo_url, token, '--name', repo_name)

            # Step 2: Create each page
            for page in pages:
                json_path = os.path.join(json_dir, page["json_file"])
                self.stdout.write(self.style.NOTICE(f"Looking for JSON file: {json_path}"))

                if not os.path.exists(json_path):
                    raise CommandError(f"❌ Missing JSON file: {json_path}")

                self.stdout.write(self.style.NOTICE(f"✅ Found JSON file: {json_path}"))
                try:
                    with open(json_path, 'r', encoding='utf-8') as f:
                        args_json = f.read()
                    self.stdout.write(self.style.NOTICE(f"📦 Loaded JSON content for: {page['name']}"))

                    self.stdout.write(
                        self.style.NOTICE(f"🚀 Creating page: {page['name']} using template '{page['template']}'..."))
                    call_command(
                        'create_page',
                        repo_name,
                        branch,
                        page["name"],
                        page["template"],
                        args_json,
                        '--username',
                        username
                    )
                    self.stdout.write(self.style.SUCCESS(f"✅ Successfully created page: {page['name']}"))

                except Exception as e:
                    self.stderr.write(self.style.ERROR(f"❌ Failed to create page {page['name']}: {e}"))
                    raise CommandError(f"Page creation failed for {page['name']}")

            self.stdout.write(self.style.SUCCESS("Ingress completed successfully for all pages."))

        except CommandError as e:
            self.stderr.write(self.style.ERROR(f"Ingress failed: {e}"))
