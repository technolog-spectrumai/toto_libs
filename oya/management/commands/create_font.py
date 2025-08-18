from django.core.management.base import BaseCommand
from oya.models import Font


class Command(BaseCommand):
    help = "Create a font with a name and CDN link"

    def add_arguments(self, parser):
        parser.add_argument('--name', type=str, required=True, help='Font name')
        parser.add_argument('--cdn', type=str, required=True, help='CDN link for the font')

    def handle(self, *args, **options):
        name = options['name']
        cdn = options['cdn']

        font, created = Font.objects.get_or_create(name=name, defaults={"cdn_link": cdn})

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created Font: {name}"))
        else:
            self.stdout.write(self.style.WARNING(f"Font already exists: {name}"))
