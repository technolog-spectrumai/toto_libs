from django.utils import timezone
from library.models import (
    ReferenceTag,
    BookReference,
    JournalReference,
    VideoReference,
    AudioReference,
    WebsiteReference,
    GenericReference,
)
from oya.ingress import IngressCommand


class Command(IngressCommand):
    help = "Creates a demo bibliography setup with sample references and tags"

    def process(self):
        self.create_dashboard_item(
            title="Bibliography",
            icon="fa-solid fa-book",
            description="A demo bibliography with references of different types.",
            link="/biblio/",
            public=False,
        )
        if not self.full:
            return

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

        journal = JournalReference.objects.create(
            title="Deep Residual Learning for Image Recognition",
            author="Kaiming He",
            journal="CVPR",
            year="2016",
            doi="10.1109/CVPR.2016.90",
            order=2,
        )
        journal.tags.add(science)

        video = VideoReference.objects.create(
            title="Design Thinking Explained",
            creator="IDEO",
            platform="YouTube",
            url="https://youtube.com/example",
            year="2020",
            order=3,
        )
        video.tags.add(design, media)

        audio = AudioReference.objects.create(
            title="Podcast on AI Ethics",
            artist="AI Now Institute",
            album="Season 2",
            url="https://example.com/podcast",
            year="2021",
            order=4,
        )
        audio.tags.add(science)

        website = WebsiteReference.objects.create(
            title="BBC News Article on Climate",
            site_name="BBC News",
            url="https://bbc.com/climate",
            accessed_date=timezone.now().date(),
            year="2022",
            order=5,
        )
        website.tags.add(science, media)

        generic = GenericReference.objects.create(
            title="UN Climate Report",
            author="United Nations",
            source_type="Report",
            url="https://un.org/climate-report",
            year="2023",
            description="Comprehensive climate change assessment",
            order=6,
        )
        generic.tags.add(science)

        print("[Ingress] Demo bibliography created with sample references.")
