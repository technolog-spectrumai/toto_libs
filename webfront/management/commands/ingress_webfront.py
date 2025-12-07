import random
from django.utils.timezone import now
from faker import Faker
from django.template import Template, Context
from oya.ingress import IngressCommand
from webfront.models import StaticPage, HtmlTemplate, DynamicPage

fake = Faker()


class Command(IngressCommand):
    help = "Seed sample data for Webfront: static pages, templates, and dynamic pages"

    # -----------------------------
    # Helpers
    # -----------------------------
    def create_templates(self, count=2):
        """
        Ensure at least `count` HTML templates exist.
        """
        templates = list(HtmlTemplate.objects.all())
        while len(templates) < count:
            name = f"Template{len(templates)+1}"
            content = """
                <h2>{{ headline }}</h2>
                {% for p in paragraphs %}
                  <p>{{ p }}</p>
                {% endfor %}
                <p><em>Published {{ published }}</em></p>
            """
            tmpl = HtmlTemplate.objects.create(name=name, content=content)
            templates.append(tmpl)
            self.stdout.write(self.style.SUCCESS(f"🖼️ Template: {tmpl.name}"))
        return templates

    def create_static_pages(self, templates, count=5):
        """
        Ensure at least `count` static pages exist.
        Render JSON data into a template to produce body HTML.
        """
        pages = list(StaticPage.objects.all())
        while len(pages) < count:
            title = fake.sentence(nb_words=3).rstrip(".")
            slug = fake.unique.slug()
            data = {
                "headline": title,
                "paragraphs": [fake.paragraph(nb_sentences=2) for _ in range(2)],
                "published": now().strftime("%B %d, %Y"),
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

    def create_dynamic_pages(self, templates, count=5):
        """
        Ensure at least `count` dynamic pages exist, linked to templates.
        """
        pages = list(DynamicPage.objects.all())
        while len(pages) < count:
            title = fake.sentence(nb_words=2).rstrip(".")
            slug = fake.unique.slug()
            data = {
                "headline": title,
                "paragraphs": [fake.paragraph(nb_sentences=2) for _ in range(2)],
                "published": now().strftime("%B %d, %Y"),
            }
            template = random.choice(templates)
            page = DynamicPage.objects.create(
                slug=slug,
                title=title,
                data=data,
                template=template,
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

        # Step 1: Templates
        templates = self.create_templates(count=2)

        # Step 2: Static Pages (rendered via template)
        self.create_static_pages(templates, count=5)

        # Step 3: Dynamic Pages
        self.create_dynamic_pages(templates, count=5)

        self.stdout.write(self.style.SUCCESS("✅ Webfront ingress complete."))
