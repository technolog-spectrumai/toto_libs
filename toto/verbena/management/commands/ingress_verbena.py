from django.utils.text import slugify
from toto.verbena.models import (
    Page, Section, Subsection,
    Image, Tag, Topic,
    Book, Article, Audio, Video
)
from toto.core.ingress import IngressCommand
from toto.socialhub.models import Person
from toto.locations.models import Address
from toto.events.models import Event
import random


class Command(IngressCommand):
    help = "Populate the database with sample Verbena Pages, Sections, Subsections and Library items"

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

        return Topic.objects.create(slug=slug, name=name, **filters)

    def process(self):
        if not self.full:
            return

        author = self.get_or_create_demo_person()
        pages = self.get_sample_pages()

        # Create library items first
        self.create_library_samples(author)

        # Create pages and sections
        for page_title, page_data in pages.items():
            if Page.objects.filter(title=page_title).exists():
                self.stdout.write(self.style.WARNING(f"⚠️ Skipped existing page: {page_title}"))
                continue

            page = self.create_page(page_title, page_data["description"])
            self.assign_tags(page, page_data["tags"])
            self.create_sections(page, page_data["sections"], author)

        self.stdout.write(self.style.SUCCESS("✅ Verbena ingress complete."))

    def get_or_create_demo_person(self):
        person, _ = Person.objects.get_or_create(
            display_name="Demo Author",
            defaults={"bio": "Automatically generated demo author."}
        )
        return person

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

    def create_library_samples(self, author):
        # Books
        for i in range(3):
            book = Book.objects.create(
                title=f"Sample Book {i+1}",
                year=2018 + i,
                publisher="Demo Publisher",
                edition=f"{i+1}th",
                isbn=f"978-3-16-14841{i}",
            )
            book.authors.add(author)
            book.tags.add(*Tag.objects.order_by("?")[:3])
            book.save()
            self.stdout.write(self.style.SUCCESS(f"📚 Created book: {book.title}"))

        # Articles
        for i in range(4):
            article = Article.objects.create(
                title=f"Sample Article {i+1}",
                year=2019 + i,
                journal="Demo Journal",
                volume=str(i+1),
                pages=f"{i*10+1}-{i*10+5}",
            )
            article.authors.add(author)
            article.tags.add(*Tag.objects.order_by("?")[:2])
            article.save()
            self.stdout.write(self.style.SUCCESS(f"📰 Created article: {article.title}"))

        # Audio
        for i in range(2):
            audio = Audio.objects.create(
                title=f"Sample Audio {i+1}",
                artist="Demo Artist",
                album=f"Demo Album {i+1}",
                duration_seconds=random.randint(120, 300)
            )
            audio.authors.add(author)
            audio.tags.add(*Tag.objects.order_by("?")[:1])
            audio.save()
            self.stdout.write(self.style.SUCCESS(f"🎵 Created audio: {audio.title}"))

        # Video
        for i in range(2):
            video = Video.objects.create(
                title=f"Sample Video {i+1}",
                director="Demo Director",
                producer=f"Demo Producer {i+1}",
                duration_seconds=random.randint(60, 600)
            )
            video.authors.add(author)
            video.tags.add(*Tag.objects.order_by("?")[:1])
            video.save()
            self.stdout.write(self.style.SUCCESS(f"🎬 Created video: {video.title}"))

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
            markdown_content = f"{content}\n\n## Lorem Ipsum\nLorem ipsum dolor sit amet."
            section = Section.objects.create(
                page=page,
                title=title,
                content=markdown_content,
                author=author,
                order=i
            )
            self.create_subsection(section)
        self.stdout.write(self.style.SUCCESS(f"📚 Added {len(sections)} sections to page: {page.title}"))

    def create_subsection(self, section):
        subsection = Subsection.objects.create(
            section=section,
            title=f"{section.title} – Details",
            content="This is a generated subsection.",
            order=1
        )
        if random.random() < 0.4:
            subsection.image = Image.objects.create(
                title=f"Image for {section.title}",
                file="verbena_images/sample.jpg"
            )
            subsection.save()

        # Random topics
        for field, queryset in [
            ("address", Address.objects.order_by("?")),
            ("person", Person.objects.order_by("?")),
            ("event", Event.objects.order_by("?")),
        ]:
            obj = queryset.first()
            if obj and random.random() < 0.3:
                topic = self.get_or_create_topic(slug_base=f"{field}-{obj.pk}", name=f"{obj}", **{field: obj})
                subsection.topics.add(topic)

        self.stdout.write(self.style.SUCCESS(f"📝 Created subsection: {subsection.title}"))