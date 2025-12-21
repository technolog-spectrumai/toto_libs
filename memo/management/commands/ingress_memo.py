from django.contrib.auth.models import User
from memo.models import MemoDeck, MemoCard, Tag
from oya.ingress import IngressCommand
import random


class Command(IngressCommand):
    help = "Populate the database with sample MemoDecks, MemoCards and Tags"

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

        decks = self.get_sample_decks()

        for deck_title, cards in decks.items():
            if MemoDeck.objects.filter(title=deck_title).exists():
                self.stdout.write(self.style.WARNING(f"⚠️ Skipped existing deck: {deck_title}"))
                continue

            deck = self.create_deck(deck_title, user, tags)
            self.create_cards(deck, cards)

        self.stdout.write(self.style.SUCCESS("✅ MemoDeck ingress complete."))

    def get_or_create_demo_user(self):
        user, _ = User.objects.get_or_create(
            username='demo_user',
            defaults={'email': 'demo@example.com'}
        )
        return user

    def get_or_create_tags(self):
        tag_names = ['django', 'python', 'study', 'backend', 'frontend', 'devtools', 'testing']
        return [Tag.objects.get_or_create(name=name)[0] for name in tag_names]

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

    def create_cards(self, deck, cards):
        for i, (card_title, card_content) in enumerate(cards, start=1):
            MemoCard.objects.create(
                deck=deck,
                title=card_title,
                content=card_content,
                order=i
            )
        self.stdout.write(self.style.SUCCESS(f"🃏 Added {len(cards)} cards to deck: {deck.title}"))
