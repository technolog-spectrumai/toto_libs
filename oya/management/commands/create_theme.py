import json
from django.core.management.base import BaseCommand
from oya.models import Font, Theme


class Command(BaseCommand):
    help = "Create a theme with custom colors and font"

    def add_arguments(self, parser):
        parser.add_argument('--name', type=str, required=True, help='Theme name')
        parser.add_argument('--font', type=str, required=True, help='Font name')
        parser.add_argument('--colors', type=str, required=True, help='JSON string of color definitions')
        parser.add_argument('--header', type=str, help='Optional JSON string for header styles')

    def handle(self, *args, **options):
        font = self.get_font(options['font'])
        if font is None:
            self.stdout.write(self.style.ERROR(f"Font '{options['font']}' not found."))
            return

        try:
            color_data = json.loads(options['colors'])
        except json.JSONDecodeError:
            self.stdout.write(self.style.ERROR("Invalid JSON for --colors"))
            return

        header_data = None
        if options.get('header'):
            try:
                header_data = json.loads(options['header'])
            except json.JSONDecodeError:
                self.stdout.write(self.style.ERROR("Invalid JSON for --header"))
                return

        theme, created = Theme.objects.get_or_create(
            name=options['name'],
            defaults={
                "theme": {"colors": color_data},
                "font": font,
                **({"header": header_data} if header_data else {})
            }
        )

        self.stdout.write(self.style.SUCCESS(f"{'Created' if created else 'Already exists'} Theme: {options['name']}"))

    def get_font(self, name):
        """Fetches a Font object by name"""
        try:
            return Font.objects.get(name=name)
        except Font.DoesNotExist:
            return None
