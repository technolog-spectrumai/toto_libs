import os, json
from django.conf import settings
from django.utils.timezone import now
from django.template import Template, Context
from django.core.management.base import BaseCommand
from webfront.models import HtmlTemplate, StaticPage, DynamicPage
from mandragora.models import Workflow, LambdaNode
from oya.ingress import IngressCommand



class Command(IngressCommand):
    help = "Sync Webfront assets (templates, pages, lambdas, charts) from filesystem"

    def sync_html_templates(self):
        html_dir = os.path.join(self.DATA_ROOT, "webfront", "html")
        schema_dir = os.path.join(self.DATA_ROOT, "webfront", "schema")
        for filename in os.listdir(html_dir):
            if not filename.endswith(".html"):
                continue
            name = os.path.splitext(filename)[0]
            content = self.read_text("webfront", "html", filename)
            schema_file = os.path.join(schema_dir, f"{name}.json")
            schema = self.read_json("webfront", "schema", f"{name}.json") if os.path.exists(schema_file) else None

            tmpl, created = HtmlTemplate.objects.get_or_create(
                name=name,
                defaults={"content": content, "json_schema": schema}
            )
            if not created:
                tmpl.content = content
                tmpl.json_schema = schema
                tmpl.save()
            self.stdout.write(self.style.SUCCESS(f"🖼️ HtmlTemplate synced: {tmpl.name}"))

    def seed_static_pages(self):
        config_dir = os.path.join(self.DATA_ROOT, "webfront", "page_config")
        for filename in os.listdir(config_dir):
            if not filename.endswith(".json"):
                continue
            name = os.path.splitext(filename)[0]
            data = self.read_json("webfront", "page_config", filename)
            tmpl = HtmlTemplate.objects.filter(name=name).first()
            if not tmpl:
                continue
            rendered = Template(tmpl.content).render(Context(data))
            page, created = StaticPage.objects.get_or_create(
                slug=name,
                defaults={"title": name.capitalize(), "body": rendered}
            )
            if not created:
                page.title = name.capitalize()
                page.body = rendered
                page.save()
            self.stdout.write(self.style.SUCCESS(f"📄 StaticPage seeded: {page.title}"))

    def seed_dynamic_pages(self):
        config_dir = os.path.join(self.DATA_ROOT, "webfront", "page_config")
        for filename in os.listdir(config_dir):
            if not filename.endswith(".json"):
                continue
            name = os.path.splitext(filename)[0]
            data = self.read_json("webfront", "page_config", filename)
            tmpl = HtmlTemplate.objects.filter(name=name).first()
            if not tmpl:
                continue
            page, created = DynamicPage.objects.get_or_create(
                slug=f"{name}-dyn",
                defaults={"title": f"{name.capitalize()} (dynamic)", "data": data, "template": tmpl}
            )
            if not created:
                page.title = f"{name.capitalize()} (dynamic)"
                page.data = data
                page.template = tmpl
                page.save()
            self.stdout.write(self.style.SUCCESS(f"⚡ DynamicPage seeded: {page.title}"))

    # -----------------------------
    # Main process
    # -----------------------------
    def process(self):
        self.sync_html_templates()
        self.seed_static_pages()
        self.seed_dynamic_pages()
        self.stdout.write(self.style.SUCCESS("✅ Webfront assets synced"))
