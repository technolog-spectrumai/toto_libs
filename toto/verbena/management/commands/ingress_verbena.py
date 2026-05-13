from django.utils.text import slugify

from toto.core.ingress import IngressCommand
from toto.library.models import Article, Book
from toto.socialhub.models import Person
from toto.verbena.models import Page, Section, Tag


class Command(IngressCommand):
    help = "Populate the database with sample Verbena pages, sections, and library items"

    def process(self):
        if not self.full:
            return

        author = self.get_or_create_demo_person()
        self.create_tags()
        self.create_library_samples(author)

        for page_title, page_data in self.get_sample_pages().items():
            if Page.objects.filter(title=page_title).exists():
                self.stdout.write(self.style.WARNING(f"⚠️  Skipped existing page: {page_title}"))
                continue

            page = Page.objects.create(title=page_title, description=page_data["description"])
            self.assign_tags(page, page_data["tags"])
            self.create_sections(page, page_data["sections"], author)
            self.stdout.write(self.style.SUCCESS(f"📄 Created page: {page_title}"))

        self.stdout.write(self.style.SUCCESS("✅ Verbena ingress complete."))

    # ────────────────────────────────────────────────
    # DEMO PERSON
    # ────────────────────────────────────────────────

    def get_or_create_demo_person(self):
        person, _ = Person.objects.get_or_create(
            display_name="Demo Author",
            defaults={"bio": "Automatically generated demo author."},
        )
        return person

    # ────────────────────────────────────────────────
    # TAGS
    # ────────────────────────────────────────────────

    def create_tags(self):
        for name in ["django", "python", "web", "basics", "study", "backend", "architecture", "dev"]:
            Tag.objects.get_or_create(slug=slugify(name), defaults={"name": name})

    # ────────────────────────────────────────────────
    # SAMPLE PAGES
    # ────────────────────────────────────────────────

    def get_sample_pages(self):
        return {
            "Django Overview": {
                "description": "A high-level overview of Django concepts.",
                "tags": ["django", "python", "web"],
                "sections": [
                    ("What is Django?", "Django is a high-level Python web framework."),
                    ("MTV Pattern", "Django uses the Model-Template-View architecture."),
                    ("ORM", "Django ORM allows interacting with the database using Python."),
                ],
            },
            "Python Essentials": {
                "description": "Core Python concepts for beginners.",
                "tags": ["python", "basics", "study"],
                "sections": [
                    ("Variables", "Python variables are dynamically typed."),
                    ("Functions", "Functions are defined using the def keyword."),
                    ("Modules", "Modules allow code organization and reuse."),
                ],
            },
            "Backend Concepts": {
                "description": "Important backend engineering principles.",
                "tags": ["backend", "architecture", "dev"],
                "sections": [
                    ("APIs", "APIs allow communication between systems."),
                    ("Databases", "Data persistence is handled by databases."),
                    ("Caching", "Caching improves performance by storing computed results."),
                ],
            },
        }

    # ────────────────────────────────────────────────
    # LIBRARY ITEMS (now Book / Article)
    # ────────────────────────────────────────────────

    def create_library_samples(self, author):
        for i in range(3):
            slug = slugify(f"sample-book-{i + 1}")
            book, created = Book.objects.get_or_create(
                slug=slug,
                defaults={
                    "title": f"Sample Book {i + 1}",
                    "year": 2018 + i,
                    "publisher": "Demo Publisher",
                    "edition": f"{i + 1}th",
                    "isbn": f"978-3-16-14841{i}",
                },
            )
            if created:
                book.authors.add(author)
                book.tags.set(Tag.objects.order_by("?")[:3])
                self.stdout.write(self.style.SUCCESS(f"📚 Created book: {book.title}"))

        for i in range(4):
            slug = slugify(f"sample-article-{i + 1}")
            article, created = Article.objects.get_or_create(
                slug=slug,
                defaults={
                    "title": f"Sample Article {i + 1}",
                    "year": 2019 + i,
                    "journal": "Demo Journal",
                    "volume": str(i + 1),
                    "pages": f"{i * 10 + 1}-{i * 10 + 5}",
                },
            )
            if created:
                article.authors.add(author)
                article.tags.set(Tag.objects.order_by("?")[:2])
                self.stdout.write(self.style.SUCCESS(f"📰 Created article: {article.title}"))

    # ────────────────────────────────────────────────
    # PAGE HELPERS
    # ────────────────────────────────────────────────

    def assign_tags(self, page, tag_names):
        page.tags.set(Tag.objects.filter(name__in=tag_names))

    def create_sections(self, page, sections, author):
        for i, (title, content) in enumerate(sections, start=1):
            Section.objects.create(page=page, title=title, content=content, author=author, order=i)
