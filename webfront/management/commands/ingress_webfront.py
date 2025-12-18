import os, json
from django.conf import settings
from django.utils.timezone import now
from django.template import Template, Context
from django.core.management.base import BaseCommand
from webfront.models import HtmlTemplate, StaticPage, DynamicPage, MetricsPage, Chart
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

    def sync_lambda_nodes(self, workflow_name="Webfront Workflow"):
        workflow, _ = Workflow.objects.get_or_create(
            name=workflow_name,
            defaults={"description": "Workflow for webfront lambdas"},
        )
        lambdas_dir = os.path.join(self.DATA_ROOT, "webfront", "lambdas")
        nodes = []
        for filename in os.listdir(lambdas_dir):
            if not filename.endswith(".py"):
                continue
            base = os.path.splitext(filename)[0]
            code = self.read_text("webfront", "lambdas", filename)
            node_name = f"{base.capitalize()}Node"
            node, created = LambdaNode.objects.get_or_create(
                workflow=workflow,
                name=node_name,
                defaults={"code": code}
            )
            if not created:
                node.code = code
                node.save()
            nodes.append(node)
            self.stdout.write(self.style.SUCCESS(f"📊 LambdaNode synced: {node.name}"))
        return nodes

    def sync_charts(self, nodes):
        config_dir = os.path.join(self.DATA_ROOT, "webfront", "lambda_config")
        charts = []
        for filename in os.listdir(config_dir):
            if not filename.endswith(".json"):
                continue
            base = os.path.splitext(filename)[0]
            config = self.read_json("webfront", "lambda_config", filename)
            node = next((n for n in nodes if n.name.lower().startswith(base.lower())), None)
            chart_type = config.get("chart_type", "bar")
            stacked = config.get("stacked", "bar")
            chart, created = Chart.objects.get_or_create(
                title=f"{base.capitalize()} Chart",
                defaults={
                    "description": f"Chart from {filename}",
                    "chart_type": chart_type,
                    "stacked": stacked,
                    "lambda_node": node,
                    "params": config,
                }
            )
            if not created:
                chart.description = f"Chart updated from {filename}"
                chart.chart_type = chart_type
                chart.stacked = stacked
                chart.lambda_node = node
                chart.params = config
                chart.save()
            charts.append(chart)
            self.stdout.write(self.style.SUCCESS(f"📈 Chart synced: {chart.title}"))
        return charts

    def ensure_metrics_page(self, charts):
        page, created = MetricsPage.objects.get_or_create(
            slug="metrics-dashboard",
            defaults={
                "title": "Metrics Dashboard",
                "description": "Charts loaded from webfront configs & lambdas",
                "order": 1,
                "created_at": now(),
            }
        )
        page.charts.set(charts)
        page.save()
        self.stdout.write(self.style.SUCCESS(f"🗂️ MetricsPage ready: {page.title}"))

    # -----------------------------
    # Main process
    # -----------------------------
    def process(self):
        self.sync_html_templates()
        self.seed_static_pages()
        self.seed_dynamic_pages()
        nodes = self.sync_lambda_nodes()
        charts = self.sync_charts(nodes)
        self.ensure_metrics_page(charts)
        self.stdout.write(self.style.SUCCESS("✅ Webfront assets synced"))
