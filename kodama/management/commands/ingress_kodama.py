from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from kodama.models import Site, Tag, Article, Section, SubSection
from django.utils.timezone import now
import random


LOREM = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
    "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
    "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat."
)

class Command(BaseCommand):
    help = "Seed Kodama with one site, tags, articles, sections, and subsections using lorem ipsum"

    def handle(self, *args, **kwargs):
        # Create or get owner
        owner, _ = User.objects.get_or_create(username='malakay', defaults={'email': 'malakay@example.com'})

        # Create site
        site, created = Site.objects.get_or_create(
            slug='kodama',
            defaults={
                'name': 'Kodama',
                'domain': 'https://kodama.local',
                'description': 'The Kodama news site',
                'creation_year': now().year,
                'active': True,
                'owner': owner
            }
        )
        self.stdout.write(self.style.SUCCESS(f"Site created: {site.name}"))

        # Create tags
        tag_names = ['Politics', 'Science', 'Culture', 'Energy']
        tags = []
        for name in tag_names:
            tag, _ = Tag.objects.get_or_create(name=name)
            tags.append(tag)
        self.stdout.write(self.style.SUCCESS(f"Tags created: {', '.join(tag.name for tag in tags)}"))

        # Create articles
        for i in range(1, 7):
            title = f"Kodama Article #{i}"
            article = Article.objects.create(
                site=site,
                title=title,
                summary=LOREM,
                author=owner
            )
            article.tags.set(random.sample(tags, k=random.randint(1, 3)))
            article.save()

            # Create sections
            for s in range(1, random.randint(2, 4)):
                section = Section.objects.create(
                    article=article,
                    order=s,
                    heading=f"Section {s} of {title}",
                    content=LOREM
                )

                # Create subsections
                for ss in range(1, random.randint(2, 3)):
                    SubSection.objects.create(
                        section=section,
                        order=ss,
                        title=f"SubSection {s}.{ss}",
                        content=LOREM
                    )

            self.stdout.write(self.style.SUCCESS(f"Article created: {article.title}"))

        self.stdout.write(self.style.SUCCESS("Kodama seeding complete with lorem ipsum."))
