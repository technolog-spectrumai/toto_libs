import json
from django.core.management.base import BaseCommand
from toto.core.models import Font, Theme, ColorMix


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
            raise RuntimeError(font)
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

        color_mix = self.get_or_create_color_mix(options['name'], color_data)

        theme, created = Theme.objects.get_or_create(
            name=options['name'],
            defaults={
                "color_mix": color_mix,
                "font": font,
                **({"header": header_data} if header_data else {})
            }
        )

        self.stdout.write(self.style.SUCCESS(f"{'Created' if created else 'Already exists'} Theme: {options['name']}"))

    def get_font(self, name):
        try:
            return Font.objects.get(name=name)
        except Font.DoesNotExist:
            return None

    def get_or_create_color_mix(self, name, colors):
        """Creates a ColorMix from a flat color dict"""
        color_fields = {f.name for f in ColorMix._meta.fields if isinstance(f, (ColorMix._meta.get_field('accent_1').__class__))}
        color_kwargs = {k.replace('-', '_'): v for k, v in colors.items() if k.replace('-', '_') in color_fields}

        return ColorMix.objects.get_or_create(name=name, defaults=color_kwargs)[0]
