from django.core.management.base import BaseCommand
from netogami.models import Repository, Codebase

class Command(BaseCommand):
    help = 'Create a new repository and sync codebase'

    def add_arguments(self, parser):
        parser.add_argument('repo_url', type=str, help='Git repository URL')
        parser.add_argument('access_token', type=str, help='Access token for the repository')
        parser.add_argument('--name', type=str, help='Optional name for the repository')

    def handle(self, *args, **options):
        repo_url = options['repo_url']
        token = options['access_token']
        name = options.get('name') or repo_url.split('/')[-1].replace('.git', '')

        repo, created = Repository.objects.get_or_create(
            name=name,
            defaults={'repo_url': repo_url, 'token': token}
        )

        if not created:
            self.stdout.write(self.style.WARNING(f"Repository '{name}' already exists."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Repository '{name}' created."))

        codebase, _ = Codebase.objects.get_or_create(repo=repo)
        codebase.update_codebase()
        self.stdout.write(self.style.SUCCESS(f"Codebase synced for '{name}'."))
