from django.utils import timezone
from django.utils.text import slugify
from django.contrib.auth.models import User
from oya.ingress import IngressCommand
from kodama.models import (
    Site,
    Tag,
    Category,
    Article,
    Section,
    SubSection,
    Menu,
    CategoryLink,
    Font,
    Theme,
)
import random
from django.utils.timezone import now

LOREM = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
    "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
    "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat."
)


class Command(IngressCommand):
    help = "Seed Kodama with a demo site, fonts, themes, tags, categories, articles, sections, subsections and menus using lorem ipsum"

    def process(self, _):
        self.owner = self.get_or_create_owner()
        fonts = self.create_fonts()
        themes = self.create_themes(fonts)
        self.site = self.get_or_create_site(themes.get("isotopic"))
        self.tags = self.create_tags()
        self.categories = self.create_categories()
        self.create_articles()
        self.create_menus()

        self.create_dashboard_item(
            title="Kodama Demo Site",
            icon="leaf",
            description="A seeded Kodama site with demo content for development and testing.",
            link=f"/sites/{self.site.pk}/"
        )

    def get_or_create_owner(self):
        owner, _ = User.objects.get_or_create(
            username='malakay',
            defaults={'email': 'malakay@example.com'}
        )
        return owner

    def get_or_create_site(self, theme):
        site, _ = Site.objects.get_or_create(
            slug='kodama',
            defaults={
                'name': 'Kodama',
                'domain': 'https://kodama.local',
                'creation_year': now().year,
                'active': True,
                'owner': self.owner,
                'theme': theme
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

    def create_fonts(self):
        fonts = {}

        fonts['Orbitron'], _ = Font.objects.get_or_create(
            name='Orbitron',
            defaults={
                'import_url': 'https://fonts.googleapis.com/css2?family=Orbitron:wght@500;700&display=swap',
                'fallback': 'sans-serif'
            }
        )

        fonts['IBM Plex Mono'], _ = Font.objects.get_or_create(
            name='IBM Plex Mono',
            defaults={
                'import_url': 'https://fonts.googleapis.com/css2?family=IBM+Plex+Mono&display=swap',
                'fallback': 'monospace'
            }
        )

        fonts['Playfair Display'], _ = Font.objects.get_or_create(
            name='Playfair Display',
            defaults={
                'import_url': 'https://fonts.googleapis.com/css2?family=Playfair+Display&display=swap',
                'fallback': 'serif'
            }
        )

        fonts['Inter'], _ = Font.objects.get_or_create(
            name='Inter',
            defaults={
                'import_url': 'https://fonts.google.com/share?selection.family=Inter:ital,opsz,wght@0,14..32,100..900;1,14..32,100..900',
                'fallback': 'sans-serif'
            }
        )

        self.stdout.write(self.style.SUCCESS(f"Fonts created: {', '.join(fonts.keys())}"))
        return fonts

    def create_themes(self, fonts):
        # Ensure the original theme is named "Isotopic"
        isotopic, created = Theme.objects.update_or_create(
            name='Isotopic',
            defaults={
                'heading_font': fonts['Orbitron'],
                'body_font': fonts['IBM Plex Mono'],

                # Light mode
                'light_bg_top': '#e0f7fa',
                'light_bg_bottom': '#ffffff',
                'light_text_main': '#2c2c2c',
                'light_text_muted': '#555555',
                'light_accent': '#0077ff',
                'light_border': '#00f0ff',
                'light_card_bg': '#f4f4f4',
                'light_footer_bg': '#f4f4f4',
                'light_nav_bg_start': '#d0eaff',
                'light_nav_bg_end': '#e0f7fa',
                'light_nav_text': '#0077ff',
                'light_nav_shadow': 'rgba(0, 119, 255, 0.25)',
                'light_banner_overlay': 'rgba(244, 244, 244, 0.25)',

                # Dark mode
                'dark_bg_top': '#0a0a0a',
                'dark_bg_bottom': '#1a1a1a',
                'dark_text_main': '#ffffff',
                'dark_text_muted': '#cccccc',
                'dark_accent': '#00ff99',
                'dark_border': '#0077ff',
                'dark_card_bg': '#1a1a1a',
                'dark_footer_bg': '#1a1a1a',
                'dark_nav_bg_start': '#003344',
                'dark_nav_bg_end': '#001a33',
                'dark_nav_text': '#00ff99',
                'dark_nav_shadow': 'rgba(0, 255, 153, 0.25)',
                'dark_banner_overlay': 'rgba(26, 26, 26, 0.5)'
            }
        )

        # Create new theme "Orbital" based on Tailwind config
        orbital, created = Theme.objects.get_or_create(
            name='Orbital',
            defaults={
                'heading_font': fonts['Orbitron'],  # Futuristic, NASA-style
                'body_font': fonts['IBM Plex Mono'],  # Terminal-style, telemetry feel

                # Light mode — Launchpad dawn
                'light_bg_top': '#dbe9f4',  # stratospheric blue
                'light_bg_bottom': '#f0f8ff',  # ion mist
                'light_text_main': '#1a1a2e',  # mission control ink
                'light_text_muted': '#4b5d67',  # telemetry gray
                'light_accent': '#ff6f00',  # booster orange
                'light_border': '#a0c4ff',  # HUD blue
                'light_card_bg': '#ffffff',  # clean module shell
                'light_footer_bg': '#e3f2fd',
                'light_nav_bg_start': '#dbe9f4',
                'light_nav_bg_end': '#f0f8ff',
                'light_nav_text': '#ff6f00',
                'light_nav_shadow': 'rgba(255, 111, 0, 0.25)',
                'light_banner_overlay': 'rgba(255, 255, 255, 0.25)',

                # Dark mode — Deep space telemetry
                'dark_bg_top': '#0b0c10',  # void black
                'dark_bg_bottom': '#1f2833',  # cockpit steel
                'dark_text_main': '#c5c6c7',  # console glow
                'dark_text_muted': '#45a29e',  # nebula teal
                'dark_accent': '#66fcf1',  # plasma blue
                'dark_border': '#2d3e50',  # hull graphite
                'dark_card_bg': '#121212',  # stealth matte
                'dark_footer_bg': '#1f2833',
                'dark_nav_bg_start': '#0b0c10',
                'dark_nav_bg_end': '#1f2833',
                'dark_nav_text': '#66fcf1',
                'dark_nav_shadow': 'rgba(102, 252, 241, 0.25)',
                'dark_banner_overlay': 'rgba(18, 18, 18, 0.5)',
            }
        )

        self.stdout.write(self.style.SUCCESS(f"{'Created' if created else 'Found'} theme: Orbital"))
        return {
            'isotopic': isotopic,
            'orbital': orbital
        }

