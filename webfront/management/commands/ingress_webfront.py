import random
from django.utils.timezone import now
from faker import Faker
from oya.ingress import IngressCommand
from webfront.models import Page

fake = Faker()


class Command(IngressCommand):
    help = "Seed sample data for Webfront: pages with slugs, titles, and HTML body content"

    # -----------------------------
    # Helpers
    # -----------------------------
    def create_pages(self, count=5):
        """
        Ensure at least `count` pages exist.
        """
        pages = list(Page.objects.all())
        while len(pages) < count:
            title = fake.sentence(nb_words=4)
            slug = fake.unique.slug()
            body = f"""
                <h2>{title}</h2>
                <p>{fake.paragraph(nb_sentences=5)}</p>
                <p><strong>Published:</strong> {now().strftime("%B %d, %Y")}</p>
            """
            page = Page.objects.create(
                slug=slug,
                title=title,
                body=body,
                created_at=now(),
            )
            pages.append(page)
            self.stdout.write(self.style.SUCCESS(f"📄 Created page: {page.title}"))
        return pages

    # -----------------------------
    # Main process
    # -----------------------------
    def process(self):
        # 📊 Dashboard block


        if not self.full:
            return

        # Step 1: Pages
        self.create_pages(count=5)

        self.stdout.write(self.style.SUCCESS("✅ Webfront ingress complete with sample pages."))
