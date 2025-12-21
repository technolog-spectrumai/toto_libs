from django.utils import timezone
from django.contrib.auth import get_user_model

from library.models import (
    ReferenceTag,
    BookReference,
    JournalReference,
    VideoReference,
    AudioReference,
    WebsiteReference,
    GenericReference,
    Library,
)

from oya.ingress import IngressCommand

User = get_user_model()


class Command(IngressCommand):
    help = "Creates a demo bibliography setup with sample references, tags, and a demo library."

    def process(self):
        if not self.full:
            return

        # Create or get demo user
        user, _ = User.objects.get_or_create(
            username="demo",
            defaults={"email": "demo@example.com"}
        )

        # Create a demo library
        library, _ = Library.objects.get_or_create(
            owner=user,
            name="Demo Library",
            defaults={"description": "Automatically generated demo reference library"}
        )

        # Create tags
        science = ReferenceTag.objects.get_or_create(name="Science")[0]
        design = ReferenceTag.objects.get_or_create(name="Design")[0]
        media = ReferenceTag.objects.get_or_create(name="Media")[0]

        # Create sample references
        book = BookReference.objects.create(
            title="The Art of Computer Programming",
            author="Donald Knuth",
            publisher="Addison-Wesley",
            year="1968",
            isbn="978-0201896831",
            order=1,
        )
        book.tags.add(science)
        library.references.add(book)

        journal = JournalReference.objects.create(
            title="Deep Residual Learning for Image Recognition",
            author="Kaiming He",
            journal="CVPR",
            year="2016",
            doi="10.1109/CVPR.2016.90",
            order=2,
        )
        journal.tags.add(science)
        library.references.add(journal)

        video = VideoReference.objects.create(
            title="Design Thinking Explained",
            creator="IDEO",
            platform="YouTube",
            url="https://youtube.com/example",
            year="2020",
            order=3,
        )
        video.tags.add(design, media)
        library.references.add(video)

        audio = AudioReference.objects.create(
            title="Podcast on AI Ethics",
            artist="AI Now Institute",
            album="Season 2",
            url="https://example.com/podcast",
            year="2021",
            order=4,
        )
        audio.tags.add(science)
        library.references.add(audio)

        website = WebsiteReference.objects.create(
            title="BBC News Article on Climate",
            sitename="BBC News",
            url="https://bbc.com/climate",
            accessed_date=timezone.now().date(),
            year="2022",
            order=5,
        )
        website.tags.add(science, media)
        library.references.add(website)

        generic = GenericReference.objects.create(
            title="UN Climate Report",
            author="United Nations",
            sourcetype="Report",
            url="https://un.org/climate-report",
            year="2023",
            description="Comprehensive climate change assessment",
            order=6,
        )
        generic.tags.add(science)
        library.references.add(generic)

        print("[Ingress] Demo bibliography and demo library created successfully.")
