from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils.text import slugify
from django.utils.timezone import now
from kodama.models import Site, Tag, Category, Article, Section, SubSection, Menu, CategoryLink
import random

LOREM = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
    "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
    "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat."
)

class Command(BaseCommand):
    help = "Seed Kodama with one site, tags, categories, articles, sections, subsections, and menu using lorem ipsum"

    def handle(self, *args, **kwargs):
        # Create or get owner
        owner, _ = User.objects.get_or_create(username='malakay', defaults={'email': 'malakay@example.com'})

        # Create site
        site, _ = Site.objects.get_or_create(
            slug='kodama',
            defaults={
                'name': 'Kodama',
                'domain': 'https://kodama.local',
                'creation_year': now().year,
                'active': True,
                'owner': owner
            }
        )
        self.stdout.write(self.style.SUCCESS(f"Site created: {site.name}"))

        # Create tags
        tag_names = ['Politics', 'Science', 'Culture', 'Energy']
        tags = [Tag.objects.get_or_create(name=name, site=site)[0] for name in tag_names]
        self.stdout.write(self.style.SUCCESS(f"Tags created: {', '.join(tag.name for tag in tags)}"))

        # Create categories
        category_names = ['Fusion', 'Fission', 'Policy', 'Innovation', 'Global Impact']
        categories = []
        for name in category_names:
            slug = slugify(name)
            category, _ = Category.objects.get_or_create(name=name, slug=slug, site=site)
            categories.append(category)
        self.stdout.write(self.style.SUCCESS(f"Categories created: {', '.join(cat.name for cat in categories)}"))

        # Create articles
        for i in range(1, 7):
            title = f"Kodama Article #{i}"
            category = random.choice(categories)
            article = Article.objects.create(
                site=site,
                title=title,
                summary=LOREM,
                author=owner,
                category=category
            )
            article.tags.set(random.sample(tags, k=random.randint(1, 3)))

            # Create sections and subsections
            for s in range(1, random.randint(2, 4)):
                section = Section.objects.create(
                    article=article,
                    order=s,
                    heading=f"Section {s} of {title}",
                    content=LOREM
                )
                for ss in range(1, random.randint(2, 3)):
                    SubSection.objects.create(
                        section=section,
                        order=ss,
                        title=f"SubSection {s}.{ss}",
                        content=LOREM
                    )

            self.stdout.write(self.style.SUCCESS(f"Article created: {article.title} (Category: {category.name})"))

        # Create a default menu and link categories
        menu, _ = Menu.objects.get_or_create(
            site=site,
            slug='main',
            defaults={'title': 'Main Navigation'}
        )

        for idx, category in enumerate(random.sample(categories, k=4)):
            CategoryLink.objects.get_or_create(
                menu=menu,
                category=category,
                defaults={'order': idx}
            )

        self.stdout.write(self.style.SUCCESS(f"Menu created: {menu.title} with linked categories"))
        self.stdout.write(self.style.SUCCESS("Kodama ingress complete."))
