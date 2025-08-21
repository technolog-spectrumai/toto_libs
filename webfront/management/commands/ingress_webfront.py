import os
import json
from django.core.management.base import BaseCommand
from django.utils.text import slugify
from django.core.exceptions import ValidationError
from webfront.models import Language, DynamicPage

class Command(BaseCommand):
    help = "Create ingress DynamicPages for Sport, Spectrum, and Basilisk"

    def add_arguments(self, parser):
        default_data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..','data'))
        parser.add_argument(
            '--data-dir',
            type=str,
            default=default_data_dir,
            help=f'Directory containing JSON files for DynamicPages (default: {default_data_dir})'
        )

    def handle(self, *args, **options):
        data_dir = options['data_dir']
        language_slug = 'en'

        language, created = Language.objects.get_or_create(
            slug=language_slug,
            defaults={"name": "English"}
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created Language: {language.name}"))
        else:
            self.stdout.write(f"Using existing Language: {language.name}")

        if not os.path.isdir(data_dir):
            self.stderr.write(self.style.ERROR(f"Data directory does not exist: {data_dir}"))
            return

        json_files = [f for f in os.listdir(data_dir) if f.endswith('.json')]

        if not json_files:
            self.stderr.write(self.style.WARNING(f"No JSON files found in {data_dir}"))
            return
        for filename in json_files:
            filepath = os.path.join(data_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                last_token = os.path.splitext(os.path.basename(filepath))[0]
                print("---->", last_token)
                slug = slugify(last_token)
                dp = DynamicPage(
                    name=last_token,
                    slug=slug,
                    language=language,
                    template_key=last_token,
                    config_json=data
                )

                #dp.full_clean()
                dp.save()
                self.stdout.write(self.style.SUCCESS(f"Created DynamicPage: {dp.name}"))
            except Exception as e:
                pass