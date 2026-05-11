from toto.verbena.models import (
    Page, Section, Subsection,
    Image, Tag, Topic,
    Book, Article, Audio, Video
)
from toto.core.ingress import IngressCommand
import random
from toto.socialhub.models import Person
from toto.locations.models import Address
from toto.events.models import Event
from django.utils.text import slugify


class Command(IngressCommand):
    help = "Populate the database with sample Verbena Pages, Sections, Subsections and Library items"

    # ────────────────────────────────────────────────
    # TOPIC CREATION
    # ────────────────────────────────────────────────

    def get_or_create_topic(self, *, slug_base, name, **filters):
        existing = Topic.objects.filter(**filters).first()
        if existing:
            return existing

        base_slug = slugify(slug_base)
        slug = base_slug
        counter = 1
        while Topic.objects.filter(slug=slug).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1

        return Topic.objects.create(
            slug=slug,
            name=name,
            **filters
        )

    # ────────────────────────────────────────────────
    # PROCESS
    # ────────────────────────────────────────────────

    def process(self):

        if not self.full:
            return

        author = self.get_or_create_demo_person()
        pages = self.get_sample_pages()
        self.create_library_samples(author)

        created_pages = []

        for page_title, page_data in pages.items():
            if Page.objects.filter(title=page_title).exists():
                self.stdout.write(self.style.WARNING(f"⚠️ Skipped existing page: {page_title}"))
                continue

            page = self.create_page(page_title, page_data["description"])
            self.assign_tags(page, page_data["tags"])
            self.create_sections(page, page_data["sections"], author)

            created_pages.append(page)

        self.stdout.write(self.style.SUCCESS("✅ Verbena ingress complete."))

    # ────────────────────────────────────────────────
    # PERSON (instead of User)
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
    # SAMPLE LIBRARY ITEMS
    # ────────────────────────────────────────────────

    def create_library_samples(self, author):
        # Create sample Books
        for i in range(3):
            book = Book.objects.create(
                title=f"Sample Book {i+1}",
                year=2020 + i,
                publisher="Demo Publisher",
                edition=f"{i+1}th",
                isbn=f"978-3-16-14841{i}",
            )
            book.authors.add(author)
            book.tags.add(*Tag.objects.order_by("?")[:2])
            book.save()
            self.stdout.write(self.style.SUCCESS(f"📚 Created book: {book.title}"))

        # Create sample Articles
        for i in range(3):
            article = Article.objects.create(
                title=f"Sample Article {i+1}",
                year=2021 + i,
                journal="Demo Journal",
                volume=str(i+1),
                pages=f"{i*10+1}-{i*10+5}",
            )
            article.authors.add(author)
            article.tags.add(*Tag.objects.order_by("?")[:2])
            article.save()
            self.stdout.write(self.style.SUCCESS(f"📰 Created article: {article.title}"))

        # Create sample Audio
        for i in range(2):
            audio = Audio.objects.create(
                title=f"Sample Audio {i+1}",
                artist="Demo Artist",
                album=f"Demo Album {i+1}",
            )
            audio.authors.add(author)
            audio.tags.add(*Tag.objects.order_by("?")[:1])
            audio.save()
            self.stdout.write(self.style.SUCCESS(f"🎵 Created audio: {audio.title}"))

        # Create sample Video
        for i in range(2):
            video = Video.objects.create(
                title=f"Sample Video {i+1}",
                director="Demo Director",
                producer=f"Demo Producer {i+1}",
            )
            video.authors.add(author)
            video.tags.add(*Tag.objects.order_by("?")[:1])
            video.save()
            self.stdout.write(self.style.SUCCESS(f"🎬 Created video: {video.title}"))

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

    def create_sections(self, page, sections, author):
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
                author=author,
                order=i
            )

            self.create_subsection(section)

        self.stdout.write(self.style.SUCCESS(
            f"📚 Added {len(sections)} sections to page: {page.title}"
        ))

    # ────────────────────────────────────────────────
    # SUBSECTION CREATION
    # ────────────────────────────────────────────────

    def create_subsection(self, section):
        subsection = Subsection.objects.create(
            section=section,
            title=f"{section.title} – Details",
            content="This is a generated subsection with additional details.",
            order=1
        )

        if random.random() < 0.4:
            subsection.image = Image.objects.create(
                title=f"Image for {section.title}",
                file="verbena_images/sample.jpg",
            )
            subsection.save()

        topic_sources = [
            ("address", Address.objects.order_by("?").first(), "address"),
            ("person",  Person.objects.order_by("?").first(),  "person"),
            ("event",   Event.objects.order_by("?").first(),   "event"),
        ]

        for prefix, obj, field in topic_sources:
            if obj and random.random() < 0.3:
                topic = self.get_or_create_topic(
                    slug_base=f"{prefix}-{obj.pk}",
                    name=f"{obj}",
                    **{field: obj}
                )
                subsection.topics.add(topic)

        self.stdout.write(self.style.SUCCESS(
            f"📝 Created subsection: {subsection.title}"
        ))