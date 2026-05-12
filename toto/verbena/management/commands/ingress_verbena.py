from django.utils.text import slugify
from toto.verbena.models import Page, Section, Tag, Reference
from toto.core.ingress import IngressCommand
from toto.socialhub.models import Person
import random


class Command(IngressCommand):
    help = "Populate the database with sample Verbena Pages, Sections, and Library items"

    # ────────────────────────────────────────────────
    # MAIN PROCESS
    # ────────────────────────────────────────────────

    def process(self):
        if not self.full:
            return

        author = self.get_or_create_demo_person()
        pages = self.get_sample_pages()

        # 1️⃣ Create library items (books and articles only)
        books, articles = self.create_library_samples(author)

        # 2️⃣ Create pages + sections
        for page_title, page_data in pages.items():
            if Page.objects.filter(title=page_title).exists():
                self.stdout.write(self.style.WARNING(f"⚠️ Skipped existing page: {page_title}"))
                continue

            page = self.create_page(page_title, page_data["description"])
            self.assign_tags(page, page_data["tags"])
            self.create_sections(page, page_data["sections"], author)

            # 3️⃣ Randomly attach references to this page
            page.references.set(random.sample(books + articles, min(len(books + articles), random.randint(1, 3))))
            page.save()

        self.stdout.write(self.style.SUCCESS("✅ Verbena ingress complete."))

    # ────────────────────────────────────────────────
    # DEMO PERSON
    # ────────────────────────────────────────────────

    def get_or_create_demo_person(self):
        person, _ = Person.objects.get_or_create(
            display_name="Demo Author",
            defaults={"bio": "Automatically generated demo author."}
        )
        return person

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
                    ("ORM", "Django ORM allows interacting with the database using Python.")
                ]
            },
            "Python Essentials": {
                "description": "Core Python concepts for beginners.",
                "tags": ["python", "basics", "study"],
                "sections": [
                    ("Variables", "Python variables are dynamically typed."),
                    ("Functions", "Functions are defined using the def keyword."),
                    ("Modules", "Modules allow code organization and reuse.")
                ]
            },
            "Backend Concepts": {
                "description": "Important backend engineering principles.",
                "tags": ["backend", "architecture", "dev"],
                "sections": [
                    ("APIs", "APIs allow communication between systems."),
                    ("Databases", "Data persistence is handled by databases."),
                    ("Caching", "Caching improves performance by storing computed results.")
                ]
            }
        }

    # ────────────────────────────────────────────────
    # CREATE LIBRARY ITEMS
    # ────────────────────────────────────────────────

    def create_library_samples(self, author):
        books = []
        for i in range(3):
            book = Reference.objects.create(
                title=f"Sample Book {i+1}",
                year=2018 + i,
                publisher="Demo Publisher",
                edition=f"{i+1}th",
                isbn=f"978-3-16-14841{i}",
            )
            book.authors.add(author)
            book.tags.add(*Tag.objects.order_by("?")[:3])
            book.save()
            books.append(book)
            self.stdout.write(self.style.SUCCESS(f"📚 Created book: {book.title}"))

        articles = []
        for i in range(4):
            article = Reference.objects.create(
                title=f"Sample Article {i+1}",
                year=2019 + i,
                journal="Demo Journal",
                volume=str(i+1),
                pages=f"{i*10+1}-{i*10+5}",
            )
            article.authors.add(author)
            article.tags.add(*Tag.objects.order_by("?")[:2])
            article.save()
            articles.append(article)
            self.stdout.write(self.style.SUCCESS(f"📰 Created article: {article.title}"))

        return books, articles

    # ────────────────────────────────────────────────
    # PAGE, TAGS, SECTIONS
    # ────────────────────────────────────────────────

    def create_page(self, title, description):
        page = Page.objects.create(title=title, description=description)
        self.stdout.write(self.style.SUCCESS(f"📄 Created page: {title}"))
        return page

    def assign_tags(self, page, tag_names):
        tag_objects = []
        for name in tag_names:
            tag, _ = Tag.objects.get_or_create(name=name)
            tag_objects.append(tag)
        page.tags.set(tag_objects)
        self.stdout.write(self.style.SUCCESS(f"🏷️ Added tags to page: {page.title}"))

    def create_sections(self, page, sections, author):
        for i, (title, content) in enumerate(sections, start=1):
            section = Section.objects.create(
                page=page,
                title=title,
                content=content,  # Trix handles headings/images inline
                author=author,
                order=i
            )
        self.stdout.write(self.style.SUCCESS(f"📚 Added {len(sections)} sections to page: {page.title}"))