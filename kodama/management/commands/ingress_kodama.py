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
    help = "Seed Kodama with one site, tags, categories, articles, sections, subsections, and multiple menus using lorem ipsum"

    def handle(self, *args, **kwargs):
        self.owner = self.get_or_create_owner()
        self.site = self.get_or_create_site()
        self.tags = self.create_tags()
        self.categories = self.create_categories()
        self.create_articles()
        self.create_menus()
        self.stdout.write(self.style.SUCCESS("Kodama ingress complete."))

    def get_or_create_owner(self):
        owner, _ = User.objects.get_or_create(
            username='malakay',
            defaults={'email': 'malakay@example.com'}
        )
        return owner

    def get_or_create_site(self):
        site, _ = Site.objects.get_or_create(
            slug='kodama',
            defaults={
                'name': 'Kodama',
                'domain': 'https://kodama.local',
                'creation_year': now().year,
                'active': True,
                'owner': self.owner
            }
        )
        self.stdout.write(self.style.SUCCESS(f"Site created: {site.name}"))
        return site

    def create_tags(self):
        tag_names = ['Politics', 'Science', 'Culture', 'Energy']
        tags = [Tag.objects.get_or_create(name=name, site=self.site)[0] for name in tag_names]
        self.stdout.write(self.style.SUCCESS(f"Tags created: {', '.join(tag.name for tag in tags)}"))
        return tags

    def create_categories(self):
        category_names = ['Fusion', 'Fission', 'Policy', 'Innovation', 'Global Impact', 'News']  # ← Added "News"
        categories = []
        for name in category_names:
            slug = slugify(name)
            category, _ = Category.objects.get_or_create(name=name, slug=slug, site=self.site)
            categories.append(category)
        self.stdout.write(self.style.SUCCESS(f"Categories created: {', '.join(cat.name for cat in categories)}"))
        return categories

    def create_articles(self):
        fruits = ['Kiwi', 'Banana', 'Cherry', 'Mango', 'Apple', 'Kiwi', "Plum"]

        for i in range(1, 7):
            fruit = random.choice(fruits)
            category = random.choice(self.categories)
            title = f"{fruit} {category.name} Article #{i}"

            article = Article.objects.create(
                site=self.site,
                title=title,
                summary=LOREM,
                author=self.owner,
                category=category
            )
            article.tags.set(random.sample(self.tags, k=random.randint(1, 3)))

            # Use fruit + category name as the first section heading
            section_heading = f"{fruit} {category.name}"
            section = Section.objects.create(
                article=article,
                order=1,
                heading=section_heading,
                content=LOREM
            )

            for ss in range(1, random.randint(2, 3)):
                SubSection.objects.create(
                    section=section,
                    order=ss,
                    title=f"SubSection 1.{ss}",
                    content=LOREM
                )

            # Add additional sections if needed
            for s in range(2, random.randint(3, 5)):
                sec = Section.objects.create(
                    article=article,
                    order=s,
                    heading=f"Section {s} of {title}",
                    content=LOREM
                )
                for ss in range(1, random.randint(2, 3)):
                    SubSection.objects.create(
                        section=sec,
                        order=ss,
                        title=f"SubSection {s}.{ss}",
                        content=LOREM
                    )

            self.stdout.write(self.style.SUCCESS(
                f"Article created: {title} (Fruit: {fruit}, Category: {category.name}, First Section: {section_heading})"
            ))

    def create_menus(self):
        # Create "News" menu as a direct link
        news_category = next((c for c in self.categories if c.name == 'News'), None)
        if news_category:
            Menu.objects.get_or_create(
                site=self.site,
                slug='news',
                defaults={
                    'title': 'News',
                    'order': 0,
                    'category_link': news_category
                }
            )
            self.stdout.write(self.style.SUCCESS(f"Direct Link Menu created: News → {news_category.name}"))

        # Define dropdown menus with curated categories
        dropdown_menus = [
            {
                'slug': 'technology',
                'title': 'Technology',
                'order': 1,
                'categories': ['Fusion', 'Innovation']
            },
            {
                'slug': 'economy',
                'title': 'Economy',
                'order': 2,
                'categories': ['Policy', 'Global Impact']
            },
            {
                'slug': 'innovation',
                'title': 'Innovation',
                'order': 3,
                'categories': ['Innovation', 'Fusion']
            },
        ]

        for spec in dropdown_menus:
            menu, _ = Menu.objects.get_or_create(
                site=self.site,
                slug=spec['slug'],
                defaults={
                    'title': spec['title'],
                    'order': spec['order'],
                    'category_link': None
                }
            )

            for idx, cat_name in enumerate(spec['categories']):
                category = next((c for c in self.categories if c.name == cat_name), None)
                if category:
                    CategoryLink.objects.get_or_create(
                        menu=menu,
                        category=category,
                        defaults={'order': idx}
                    )

            self.stdout.write(self.style.SUCCESS(
                f"Dropdown Menu created: {spec['title']} with categories: {', '.join(spec['categories'])}"
            ))



