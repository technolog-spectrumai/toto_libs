from django.contrib.auth.models import User
from verbena.models import Page, Section, Image, Tag
from oya.ingress import IngressCommand
import random


class Command(IngressCommand):
    help = "Populate the database with sample Verbena Pages, Sections, and Images"

    def process(self):
        self.create_dashboard_item(
            title="Verbena",
            icon="fa-solid fa-book",
            description="Wiki-style knowledge pages.",
            link="/verbena/"
        )
        if not self.full:
            return

        user = self.get_or_create_demo_user()
        pages = self.get_sample_pages()

        for page_title, page_data in pages.items():
            if Page.objects.filter(title=page_title).exists():
                self.stdout.write(self.style.WARNING(f"⚠️ Skipped existing page: {page_title}"))
                continue

            page = self.create_page(page_title, page_data["description"], user)
            self.assign_tags(page, page_data["tags"])
            self.create_sections(page, page_data["sections"])

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
    # CREATION HELPERS
    # ────────────────────────────────────────────────

    def create_page(self, title, description, user):
        page = Page.objects.create(
            title=title,
            description=description,
            author=user,
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

    def create_sections(self, page, sections):
        for i, (title, content) in enumerate(sections, start=1):
            section = Section.objects.create(
                page=page,
                title=title,
                content=content,
                order=i
            )

            # Randomly attach an image to ~40% of sections
            if random.random() < 0.4:
                self.create_image(section)

        self.stdout.write(self.style.SUCCESS(
            f"📚 Added {len(sections)} sections to page: {page.title}"
        ))

    def create_image(self, section):
        Image.objects.create(
            section=section,
            title=f"Image for {section.title}",
            file="verbena_images/sample.jpg",
            order=1
        )
        self.stdout.write(self.style.SUCCESS(f"🖼️ Added image to section: {section.title}"))
