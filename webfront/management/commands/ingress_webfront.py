import random
from django.utils.timezone import now
from faker import Faker
from django.template import Template, Context
from oya.ingress import IngressCommand
from webfront.models import StaticPage, HtmlTemplate, DynamicPage
from mandragora.models import Workflow, LambdaNode

fake = Faker()


class Command(IngressCommand):
    help = "Seed sample data for Webfront: static pages, templates, dynamic pages, chart templates, and a special LambdaNode"

    # -----------------------------
    # Helpers
    # -----------------------------
    def create_templates(self, count=2):
        templates = list(HtmlTemplate.objects.all())
        while len(templates) < count:
            name = f"Template{len(templates)+1}"
            content = """
                {% load include_from_db %}
                <h2>{{ headline }}</h2>
                {% for p in paragraphs %}
                  <p>{{ p }}</p>
                {% endfor %}
                <p><em>Published {{ published }}</em></p>
                <p><strong>{{ extra_message }}</strong></p>
            """
            schema = {
                "type": "object",
                "properties": {
                    "headline": {"type": "string"},
                    "paragraphs": {"type": "array", "items": {"type": "string"}},
                    "published": {"type": "string"},
                    "extra_message": {"type": "string"},
                },
                "required": ["headline", "paragraphs", "published"],
            }
            tmpl = HtmlTemplate.objects.create(
                name=name,
                content=content,
                json_schema=schema,
            )
            templates.append(tmpl)
            self.stdout.write(self.style.SUCCESS(f"🖼️ Template: {tmpl.name}"))
        return templates

    def create_chart_templates(self):
        """
        Create chart templates for line, pie, and bar charts.
        """
        chart_templates = []
        chart_types = [
            ("LineChartTemplate", "line"),
            ("PieChartTemplate", "pie"),
            ("BarChartTemplate", "bar"),
        ]

        for name, chart_type in chart_types:
            if not HtmlTemplate.objects.filter(name=name).exists():
                content = """
                    <h2>{{ title }}</h2>
                    <canvas id="chartCanvas"></canvas>
                """
                script = f"""
                    const ctx = document.getElementById('chartCanvas').getContext('2d');
                    new Chart(ctx, {{
                        type: '{chart_type}',
                        data: {{
                            labels: {{ labels|safe }},
                            datasets: [{{
                                label: '{{ dataset_label }}',
                                data: {{ data|safe }},
                                backgroundColor: {{ colors|safe }},
                            }}]
                        }},
                        options: {{ options|safe }}
                    }});
                """
                schema = {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "labels": {"type": "array", "items": {"type": "string"}},
                        "dataset_label": {"type": "string"},
                        "data": {"type": "array", "items": {"type": "number"}},
                        "colors": {"type": "array", "items": {"type": "string"}},
                        "options": {"type": "object"},
                    },
                    "required": ["title", "labels", "data"],
                }
                tmpl = HtmlTemplate.objects.create(
                    name=name,
                    content=content,
                    script=script,
                    json_schema=schema,
                )
                chart_templates.append(tmpl)
                self.stdout.write(self.style.SUCCESS(f"📊 Chart template: {tmpl.name}"))
        return chart_templates

    def create_static_pages(self, templates, count=5):
        pages = list(StaticPage.objects.all())
        while len(pages) < count:
            title = fake.sentence(nb_words=3).rstrip(".")
            slug = fake.unique.slug()
            data = {
                "headline": title,
                "paragraphs": [fake.paragraph(nb_sentences=2) for _ in range(2)],
                "published": now().strftime("%B %d, %Y"),
                "extra_message": "Static content only",
            }
            template = random.choice(templates)
            rendered_body = Template(template.content).render(Context(data))

            page = StaticPage.objects.create(
                slug=slug,
                title=title,
                body=rendered_body,
                created_at=now(),
            )
            pages.append(page)
            self.stdout.write(self.style.SUCCESS(f"📄 Static page: {page.title}"))
        return pages

    def create_special_lambda_node(self, workflow_name="Webfront Workflow"):
        """
        Create a special LambdaNode that enriches context with an extra message.
        """
        workflow, _ = Workflow.objects.get_or_create(
            name=workflow_name,
            defaults={"description": "Workflow for webfront dynamic pages"},
        )

        node, _ = LambdaNode.objects.get_or_create(
            workflow=workflow,
            name="AddExtraMessage",
            defaults={
                "is_initial": False,
                "is_final": False,
                "code": """
def main(context):
    # Add an extra message based on request user
    user = getattr(context.get('request'), 'user', None)
    username = getattr(user, 'username', 'guest') if user else 'guest'
    return {"extra_message": f"Hello from LambdaNode, {username}!"}
""",
            },
        )
        self.stdout.write(self.style.SUCCESS(f"✨ Special LambdaNode: {node.name}"))
        return node

    def create_dynamic_pages(self, templates, special_node, count=5):
        pages = list(DynamicPage.objects.all())
        while len(pages) < count:
            title = fake.sentence(nb_words=2).rstrip(".")
            slug = fake.unique.slug()

            # If chart template, seed chart data
            if "ChartTemplate" in random.choice(templates).name:
                labels = ["Jan", "Feb", "Mar", "Apr", "May"]
                data = [random.randint(10, 100) for _ in labels]
                colors = ["#FF6384", "#36A2EB", "#FFCE56", "#4BC0C0", "#9966FF"]
                dataset_label = "Sample Data"
                page_data = {
                    "title": title,
                    "labels": labels,
                    "dataset_label": dataset_label,
                    "data": data,
                    "colors": colors,
                    "options": {},
                }
            else:
                page_data = {
                    "headline": title,
                    "paragraphs": [fake.paragraph(nb_sentences=2) for _ in range(2)],
                    "published": now().strftime("%B %d, %Y"),
                }

            template = random.choice(templates)
            page = DynamicPage.objects.create(
                slug=slug,
                title=title,
                data=page_data,
                template=template,
                lambda_node=special_node,
                created_at=now(),
            )
            pages.append(page)
            self.stdout.write(self.style.SUCCESS(f"⚡ Dynamic page: {page.title}"))
        return pages

    # -----------------------------
    # Main process
    # -----------------------------
    def process(self):
        if not self.full:
            return

        templates = self.create_templates(count=2)
        chart_templates = self.create_chart_templates()
        self.create_static_pages(templates, count=5)

        special_node = self.create_special_lambda_node()
        self.create_dynamic_pages(templates + chart_templates, special_node, count=5)

        self.stdout.write(self.style.SUCCESS("✅ Webfront ingress complete."))
