from django.utils.text import slugify

from toto.ingress import IngressCommand
from toto.people.models import Person

from toto.palimpsest.models import Page, Section, Tag


class Command(IngressCommand):
    help = "Seed Palimpsest with sample pages, sections, authors, and tags."

    def process(self):
        if not self.full:
            return

        author = self.get_or_create_demo_person()
        tags = self.create_tags()

        for page_data in self.sample_pages():
            page, created = Page.objects.get_or_create(
                slug=slugify(page_data["title"]),
                defaults={
                    "title": page_data["title"],
                    "description": page_data["description"],
                },
            )

            if not created:
                self.stdout.write(self.style.WARNING(f"Skipped existing Palimpsest piece: {page.title}"))
                continue

            page.tags.set([tags[name] for name in page_data["tags"]])

            for order, section_data in enumerate(page_data["sections"], start=1):
                section = Section.objects.create(
                    page=page,
                    title=section_data["title"],
                    content=section_data["content"],
                    author=author,
                    order=order,
                )
                section.tags.set([tags[name] for name in section_data.get("tags", page_data["tags"])])

            self.stdout.write(self.style.SUCCESS(f"Created Palimpsest piece: {page.title}"))

        self.stdout.write(self.style.SUCCESS("Palimpsest ingress complete."))

    def get_or_create_demo_person(self):
        person, _ = Person.objects.get_or_create(
            display_name="Palimpsest Editor",
            defaults={"bio": "A demo editor for the close web publication."},
        )
        return person

    def create_tags(self):
        names = [
            "close-web",
            "field-notes",
            "craft",
            "community",
            "systems",
            "letters",
        ]
        tags = {}
        for name in names:
            tag, _ = Tag.objects.get_or_create(
                slug=slugify(name),
                defaults={"name": name.replace("-", " ").title()},
            )
            tags[name] = tag
        return tags

    def sample_pages(self):
        return [
            {
                "title": "The Close Web Is a Room, Not a Stage",
                "description": "A short argument for publishing that favors continuity, trust, and return visits over reach.",
                "tags": ["close-web", "community"],
                "sections": [
                    {
                        "title": "A smaller audience can hold more context",
                        "content": (
                            "<div>Most writing tools quietly assume that publishing means leaving the room. "
                            "Palimpsest starts from the opposite instinct: the best pieces are often written "
                            "for people who already share some history with the writer.</div>"
                        ),
                    },
                    {
                        "title": "Sections beat hot takes",
                        "content": (
                            "<div>A piece can begin as a note, become a section, and later gather replies, "
                            "references, and edits. The modular section is the basic unit because closeness "
                            "is built by returning, not by announcing.</div>"
                        ),
                        "tags": ["craft", "close-web"],
                    },
                ],
            },
            {
                "title": "Notes From the Margin of a Shared Notebook",
                "description": "A field note on writing with visible seams, side paths, and unfinished edges.",
                "tags": ["field-notes", "craft"],
                "sections": [
                    {
                        "title": "The margin is part of the text",
                        "content": (
                            "<div>When a thought is still warm, hiding every trace of its formation can make "
                            "it less useful. A margin note says: here is where the idea entered the room.</div>"
                        ),
                    },
                    {
                        "title": "Useful incompleteness",
                        "content": (
                            "<div>Not every section needs to close the loop. Some sections are invitations: "
                            "questions, sketches, partial maps, or careful uncertainty left in public view.</div>"
                        ),
                        "tags": ["letters", "craft"],
                    },
                ],
            },
            {
                "title": "Maintenance as a Publishing Practice",
                "description": "Why the best communal writing systems make revision feel ordinary.",
                "tags": ["systems", "community"],
                "sections": [
                    {
                        "title": "Revision should be a first-class path",
                        "content": (
                            "<div>If a platform makes editing feel like an exception, people stop tending "
                            "their writing. Palimpsest treats each piece as something that can be maintained.</div>"
                        ),
                    },
                    {
                        "title": "A gentle admin is still an admin",
                        "content": (
                            "<div>The admin remains the canonical workbench, but the user-facing editor matters. "
                            "A close platform should let people adjust the living text without changing rooms.</div>"
                        ),
                        "tags": ["systems", "close-web"],
                    },
                ],
            },
        ]
