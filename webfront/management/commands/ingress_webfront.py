import os
import json
from django.utils.text import slugify
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from webfront.models import Language, DynamicPage, PageGenerator
from oya.ingress import IngressCommand  # Your custom base class


class Command(IngressCommand):
    help = "Populate the database with DynamicPages for Sport, Spectrum, and Basilisk"

    def add_arguments(self, parser):
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'data'))
        parser.add_argument(
            '--htmldir',
            type=str,
            default=os.path.join(base_dir, 'html'),
            help='Directory containing HTML templates for PageGenerators'
        )
        parser.add_argument(
            '--schemadir',
            type=str,
            default=os.path.join(base_dir, 'schema'),
            help='Directory containing JSON schemas for PageGenerators'
        )
        parser.add_argument(
            '--configdir',
            type=str,
            default=os.path.join(base_dir, 'config'),
            help='Directory containing config JSON files for DynamicPages'
        )

    def process(self, options):
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'data'))
        html_dir = options.get('htmldir', os.path.join(base_dir, "html"))
        schema_dir = options.get('schemadir', os.path.join(base_dir, "schema"))
        config_dir = options.get('configdir', os.path.join(base_dir, "config"))
        language_slug = 'en'

        language, _ = Language.objects.get_or_create(
            slug=language_slug,
            defaults={"name": "English"}
        )
        self.stdout.write(self.style.SUCCESS(f"🌐 Using Language: {language.name}"))

        # Validate directories
        if not all(map(os.path.isdir, [html_dir, schema_dir, config_dir])):
            self.stderr.write(self.style.ERROR("❌ One or more data directories do not exist."))
            return

        config_files = [f for f in os.listdir(config_dir) if f.endswith('.json')]
        if not config_files:
            self.stderr.write(self.style.WARNING(f"⚠️ No config JSON files found in {config_dir}"))
            return

        for filename in config_files:
            name_token = os.path.splitext(filename)[0]
            slug = slugify(name_token)

            html_path = os.path.join(html_dir, f"{name_token}.html")
            schema_path = os.path.join(schema_dir, f"{name_token}.json")
            config_path = os.path.join(config_dir, filename)

            if not os.path.isfile(html_path):
                self.stderr.write(self.style.WARNING(f"⚠️ Missing HTML template for: {name_token}"))
                continue

            try:
                with open(html_path, 'r', encoding='utf-8') as f:
                    html_template = f.read()
                with open(config_path, 'r', encoding='utf-8') as f:
                    config_json = json.load(f)

                # Load schema if available
                json_schema = None
                if os.path.isfile(schema_path):
                    with open(schema_path, 'r', encoding='utf-8') as f:
                        json_schema = json.load(f)

                generator, created = PageGenerator.objects.get_or_create(
                    slug=slug,
                    defaults={
                        'name': name_token,
                        'check_schema': bool(json_schema),
                        'json_schema': json_schema or {},
                    }
                )

                if created or not generator.html_template_file:
                    generator.html_template_file.save(
                        f"{slug}.html",
                        ContentFile(html_template),
                        save=True
                    )
                    if not created:
                        generator.save()

                dp = DynamicPage(
                    name=name_token,
                    slug=slug,
                    language=language,
                    generator=generator,
                    config_json=config_json
                )

                dp.full_clean()
                dp.save()
                self.stdout.write(self.style.SUCCESS(f"📄 Created DynamicPage: {dp.name}"))

            except ValidationError as ve:
                self.stderr.write(self.style.ERROR(f"❌ Validation failed for {name_token}: {ve}"))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"❌ Error processing {name_token}: {e}"))

        self.stdout.write(self.style.SUCCESS("✅ DynamicPage ingress complete."))
