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
        for i in range(1, 7):
            title = f"Kodama Article #{i}"
            category = random.choice(self.categories)
            article = Article.objects.create(
                site=self.site,
                title=title,
                summary=LOREM,
                author=self.owner,
                category=category
            )
            article.tags.set(random.sample(self.tags, k=random.randint(1, 3)))

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

    def create_menus(self):
        # Create explicit "News" menu with direct category link — always first
        news_category = next((c for c in self.categories if c.name == 'News'), None)
        if news_category:
            news_menu, _ = Menu.objects.get_or_create(
                site=self.site,
                slug='news',
                defaults={
                    'title': 'News',
                    'order': 0,
                    'category_link': news_category
                }
            )
            self.stdout.write(self.style.SUCCESS(f"Direct Link Menu created: News → {news_category.name}"))

        # Shift other menus to start from order 1
        menu_specs = [
            ('main', 'Main Navigation', 1),
            ('footer', 'Footer Links', 2),
            ('topics', 'Topics Menu', 3),
        ]

        for slug, title, order in menu_specs:
            is_direct_link = random.choice([True, False])
            category_link = random.choice(self.categories) if is_direct_link else None

            menu, _ = Menu.objects.get_or_create(
                site=self.site,
                slug=slug,
                defaults={
                    'title': title,
                    'order': order,
                    'category_link': category_link
                }
            )

            if not is_direct_link:
                linked_categories = random.sample(self.categories, k=min(3, len(self.categories)))
                for idx, category in enumerate(linked_categories):
                    CategoryLink.objects.get_or_create(
                        menu=menu,
                        category=category,
                        defaults={'order': idx}
                    )
                self.stdout.write(self.style.SUCCESS(
                    f"Dropdown Menu created: {menu.title} with {len(linked_categories)} categories"
                ))
            else:
                self.stdout.write(self.style.SUCCESS(
                    f"Direct Link Menu created: {menu.title} → {category_link.name}"
                ))


