from django.contrib.auth.models import User
from toto.verbena.models import (
    Page, Section, Subsection,
    Image, Tag, Topic,
    Book, Chapter
)
from toto.core.ingress import IngressCommand
import random
from toto.socialhub.models import Person
from toto.locations.models import Address
from toto.events.models import Event
from django.utils.text import slugify


class Command(IngressCommand):
    help = "Populate the database with sample Verbena Pages, Sections, Subsections, and Books"

    def process(self):

        if not self.full:
            return

        user = self.get_or_create_demo_user()
        pages = self.get_sample_pages()

        created_pages = []

        # Create Pages
        for page_title, page_data in pages.items():
            if Page.objects.filter(title=page_title).exists():
                self.stdout.write(self.style.WARNING(f"⚠️ Skipped existing page: {page_title}"))
                continue

            page = self.create_page(page_title, page_data["description"])
            self.assign_tags(page, page_data["tags"])
            self.create_sections(page, page_data["sections"], user)

            created_pages.append(page)

        # Create Books using the created pages
        self.create_sample_books(created_pages)

        self.stdout.write(self.style.SUCCESS("✅ Verbena ingress complete."))

    # ────────────────────────────────────────────────
    # USERS
    # ────────────────────────────────────────────────

    def get_or_create_demo_user(self):
        user, _ = User.objects.get_or_create(
            username="demo_user",
            defaults={"email": "demo@example.com"}
        )
        return user

    # ────────────────────────────────────────────────
    # SAMPLE DATA
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
    # PAGE CREATION
    # ────────────────────────────────────────────────

    def create_page(self, title, description):
        page = Page.objects.create(
            title=title,
            description=description,
        )
        self.stdout.write(self.style.SUCCESS(f"📄 Created page: {title}"))
        return page

    def assign_tags(self, page, tag_names):
        tag_objects = []
        for name in tag_names:
            tag, _ = Tag.objects.get_or_create(name=name)
            tag_objects.append(tag)

        page.tags.set(tag_objects)
        self.stdout.write(self.style.SUCCESS(f"🏷️ Added tags to page: {page.title}"))

    def create_sections(self, page, sections, user):
        for i, (title, content) in enumerate(sections, start=1):

            markdown_lorem = (
                f"{content}\n\n"
                "## Lorem Ipsum\n"
                "Lorem ipsum dolor sit amet, **consectetur adipiscing elit**. "
                "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.\n\n"
                "> This is a blockquote example.\n\n"
                "### Code Example\n"
                "```python\n"
                "def example():\n"
                "    return 'Hello, Markdown!'\n"
                "```\n"
            )

            section = Section.objects.create(
                page=page,
                title=title,
                content=markdown_lorem,
                author=user,
                order=i
            )

            self.create_subsection(section)

        self.stdout.write(self.style.SUCCESS(
            f"📚 Added {len(sections)} sections to page: {page.title}"
        ))

    def create_subsection(self, section):
        subsection = Subsection.objects.create(
            section=section,
            title=f"{section.title} – Details",
            content="This is a generated subsection with additional details.",
            order=1
        )

        # Optional image
        if random.random() < 0.4:
            image = Image.objects.create(
                title=f"Image for {section.title}",
                file="verbena_images/sample.jpg",
            )
            subsection.image = image
            subsection.save()

        # ────────────────────────────────────────────────
        # OPTIONAL TOPICS (Address, Person, Event)
        # ────────────────────────────────────────────────

        # Address → Topic
        if random.random() < 0.3:
            address = Address.objects.order_by("?").first()
            if address:
                slug = slugify(f"address-{address.pk}")
                topic, _ = Topic.objects.get_or_create(
                    slug=slug,
                    defaults={
                        "name": f"Address {address}",
                        "address": address
                    }
                )
                subsection.topics.add(topic)

        # Person → Topic
        if random.random() < 0.3:
            person = Person.objects.order_by("?").first()
            if person:
                slug = slugify(f"person-{person.pk}")
                topic, _ = Topic.objects.get_or_create(
                    slug=slug,
                    defaults={
                        "name": f"Person {person.display_name}",
                        "person": person
                    }
                )
                subsection.topics.add(topic)

        # Event → Topic
        if random.random() < 0.3:
            event = Event.objects.order_by("?").first()
            if event:
                slug = slugify(f"event-{event.pk}")
                topic, _ = Topic.objects.get_or_create(
                    slug=slug,
                    defaults={
                        "name": f"Event {event.title}",
                        "event": event
                    }
                )
                subsection.topics.add(topic)

        subsection.save()

        self.stdout.write(self.style.SUCCESS(
            f"📝 Created subsection: {subsection.title}"
        ))

    # ────────────────────────────────────────────────
    # BOOK CREATION
    # ────────────────────────────────────────────────

    def create_sample_books(self, pages):
        """
        Creates sample Books and assigns Pages as ordered Chapters.
        """

        if not pages:
            return

        # Example books
        books = {
            "Python Handbook": {
                "description": "A structured guide to Python fundamentals.",
                "tags": ["python", "study"],
                "pages": ["Python Essentials", "Backend Concepts"]
            },
            "Django Mastery": {
                "description": "A complete guide to Django concepts.",
                "tags": ["django", "web"],
                "pages": ["Django Overview", "Backend Concepts"]
            }
        }

        for book_title, data in books.items():

            if Book.objects.filter(title=book_title).exists():
                self.stdout.write(self.style.WARNING(f"⚠️ Skipped existing book: {book_title}"))
                continue

            book = Book.objects.create(
                title=book_title,
                description=data["description"]
            )

            # Assign tags
            tag_objects = []
            for name in data["tags"]:
                tag, _ = Tag.objects.get_or_create(name=name)
                tag_objects.append(tag)
            book.tags.set(tag_objects)

            # Add chapters
            for order, page_title in enumerate(data["pages"], start=1):
                page = Page.objects.filter(title=page_title).first()
                if page:
                    Chapter.objects.create(
                        book=book,
                        page=page,
                        order=order
                    )

            self.stdout.write(self.style.SUCCESS(f"📘 Created book: {book_title}"))
