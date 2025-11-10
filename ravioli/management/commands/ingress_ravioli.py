from django.contrib.auth.models import User
from django.utils import timezone
from oya.ingress import IngressCommand

from ravioli.graph import Note, Tag, NoteRel


class Command(IngressCommand):
    help = "Creates a demo Intel Notes graph with sample notes and tags"

    def process(self, _):
        # Add a dashboard item for Intel Notes
        self.create_dashboard_item(
            title="Intel Notes",
            icon="fa-solid fa-note-sticky",
            description="A demo graph of intel notes and tags.",
            link="/ravioli/notes/"
        )

        if not self.full:
            return

        # Ensure demo user exists
        user, _ = User.objects.get_or_create(
            username="admin",
            defaults={"email": "demo@example.com"}
        )

        # Create demo notes
        note1 = Note(
            title="Intel Report A",
            content="Observation about suspicious activity",
            category="Intel",
            metadata={"created_by": user.username, "created_at": str(timezone.now())}
        ).save()

        note2 = Note(
            title="Intel Report B",
            content="Follow-up analysis of activity",
            category="Observation",
            metadata={"created_by": user.username, "created_at": str(timezone.now())}
        ).save()

        note3 = Note(
            title="Intel Report C",
            content="Summary of findings",
            category="Report",
            metadata={"created_by": user.username, "created_at": str(timezone.now())}
        ).save()

        # Create tags
        tag_security = Tag(name="Security").save()
        tag_network = Tag(name="Network").save()

        # Connect notes with relationships
        note1.references.connect(note2, {
            "relation_type": "REFERENCES",
            "metadata": {"confidence": 0.9}
        })

        note2.related.connect(note3, {
            "relation_type": "RELATED_TO",
            "metadata": {"confidence": 0.8}
        })

        note1.tagged.connect(tag_security, {
            "relation_type": "TAGGED_WITH",
            "metadata": {"source": "analyst"}
        })

        note2.tagged.connect(tag_network, {
            "relation_type": "TAGGED_WITH",
            "metadata": {"source": "system"}
        })

        self.stdout.write(self.style.SUCCESS("Demo Intel Notes graph created successfully."))
