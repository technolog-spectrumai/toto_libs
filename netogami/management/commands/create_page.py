import json
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from netogami.models import Repository, Codebase, Template, TemplateArtifact, Page

class Command(BaseCommand):
    help = 'Create a page from a template file in a repository'

    def add_arguments(self, parser):
        parser.add_argument('repo_name', type=str)
        parser.add_argument('branch', type=str)
        parser.add_argument('file_name', type=str)
        parser.add_argument('page_name', type=str)
        parser.add_argument('args_json', type=str)
        parser.add_argument('--username', type=str, default='admin')

    def handle(self, *args, **options):
        repo_name = options['repo_name']
        branch = options['branch']
        file_name = options['file_name']
        page_name = options['page_name']
        args_json = options['args_json']
        username = options['username']

        try:
            repo = Repository.objects.get(name=repo_name)
            codebase, _ = Codebase.objects.get_or_create(repo=repo, branch=branch)
            codebase.update_codebase()

            template, _ = Template.objects.get_or_create(name=page_name)
            artifact, _ = TemplateArtifact.objects.get_or_create(
                source=codebase,
                target=template,
                file_path=file_name
            )

            artifact.sync_template()

            author = User.objects.get(username=username)
            data = json.loads(args_json)

            page = Page.objects.create(
                template=template,
                author=author,
                data=data,
                language=data.get('language', 'en')
            )

            self.stdout.write(self.style.SUCCESS(f"Page '{page.slug}' created successfully."))

        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Error: {e}"))
