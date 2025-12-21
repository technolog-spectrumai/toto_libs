from django.contrib.auth.models import User
from memo.models import MemoDeck, MemoCard, Tag, MermaidChart
from oya.ingress import IngressCommand
import random


class Command(IngressCommand):
    help = "Populate the database with sample MemoDecks, MemoCards, Tags, and MermaidCharts"

    def process(self):
        self.create_dashboard_item(
            title="MemoDecks",
            icon="fa-solid fa-eye",
            description="Company presentations.",
            link="/memo/"
        )
        if not self.full:
            return

        user = self.get_or_create_demo_user()
        tags = self.get_or_create_tags()
        charts = self.get_or_create_charts()

        decks = self.get_sample_decks()

        for deck_title, cards in decks.items():
            if MemoDeck.objects.filter(title=deck_title).exists():
                self.stdout.write(self.style.WARNING(f"⚠️ Skipped existing deck: {deck_title}"))
                continue

            deck = self.create_deck(deck_title, user, tags)
            self.create_cards(deck, cards, charts)

        self.stdout.write(self.style.SUCCESS("✅ MemoDeck ingress complete."))

    # ────────────────────────────────────────────────
    # USERS & TAGS
    # ────────────────────────────────────────────────

    def get_or_create_demo_user(self):
        user, _ = User.objects.get_or_create(
            username='demo_user',
            defaults={'email': 'demo@example.com'}
        )
        return user

    def get_or_create_tags(self):
        tag_names = ['django', 'python', 'study', 'backend', 'frontend', 'devtools', 'testing']
        return [Tag.objects.get_or_create(name=name)[0] for name in tag_names]

    # ────────────────────────────────────────────────
    # SAMPLE MERMAID CHARTS
    # ────────────────────────────────────────────────

    def get_or_create_charts(self):
        samples = [
            (
                "Simple Flow",
                "Basic flowchart example",
                "graph TD; A[Start] --> B[Process]; B --> C[End];"
            ),
            (
                "Decision Tree",
                "A branching decision example",
                "graph TD; A -->|Yes| B; A -->|No| C;"
            ),
            (
                "Sequence Example",
                "Basic sequence diagram",
                "sequenceDiagram; Alice->>Bob: Hello Bob; Bob-->>Alice: Hi Alice;"
            ),
        ]

        charts = []
        for title, desc, code in samples:
            chart, _ = MermaidChart.objects.get_or_create(
                title=title,
                defaults={"description": desc, "code": code}
            )
            charts.append(chart)

        return charts

    # ────────────────────────────────────────────────
    # SAMPLE DECKS & CARDS
    # ────────────────────────────────────────────────

    def get_sample_decks(self):
        return {
            'Django Basics': [
                ('What is Django?', 'Django is a high-level Python web framework.'),
                ('What is a QuerySet?', 'A QuerySet is a collection of database queries.'),
                ('What is a model?', 'A model is a Python class that maps to a database table.')
            ],
            'Python Tricks': [
                ('List Comprehensions', 'Concise way to create lists.'),
                ('Decorators', 'Functions that modify other functions.'),
                ('Generators', 'Functions that yield values lazily.')
            ],
            'Testing Strategies': [
                ('Unit Testing', 'Testing individual units of code.'),
                ('Integration Testing', 'Testing combined parts of a system.'),
                ('Mocking', 'Simulating parts of code for isolated testing.')
            ]
        }

    def create_deck(self, title, user, tags):
        deck = MemoDeck.objects.create(
            title=title,
            description=f"A deck about {title}.",
            author=user
        )
        deck.tags.set(random.sample(tags, k=min(3, len(tags))))
        self.stdout.write(self.style.SUCCESS(f"📘 Created deck: {title}"))
        return deck

    def create_cards(self, deck, cards, charts):
        for i, (card_title, card_content) in enumerate(cards, start=1):

            # Randomly attach a Mermaid chart to ~50% of cards
            chart = random.choice(charts) if random.random() < 0.5 else None

            MemoCard.objects.create(
                deck=deck,
                title=card_title,
                content=card_content,
                order=i,
                chart=chart
            )

        self.stdout.write(self.style.SUCCESS(
            f"🃏 Added {len(cards)} cards to deck: {deck.title}"
        ))
