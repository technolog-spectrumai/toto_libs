from django.utils.text import slugify
from django.contrib.auth.models import User
from memo.models import MemoDeck, MemoCard, Tag
from oya.ingress import IngressCommand
import random


class Command(IngressCommand):
    help = "Populate the database with sample MemoDecks, MemoCards, and Tags"

    def process(self, _):
        # Dashboard block
        self.create_dashboard_item(
            title="MemoDecks",
            icon="book-open",
            description="Creates sample decks with cards and tags for demo/testing.",
            link="/memo/"
        )

        # Ensure demo user exists
        user, _ = User.objects.get_or_create(
            username='demo_user',
            defaults={'email': 'demo@example.com'}
        )

        # Create tags
        tag_names = ['django', 'python', 'study', 'backend', 'frontend', 'devtools', 'testing']
        tags = [Tag.objects.get_or_create(name=name)[0] for name in tag_names]

        # Sample decks and cards
        decks = {
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

        # Create decks and cards
        for deck_title, cards in decks.items():
            slug = slugify(deck_title)
            if MemoDeck.objects.filter(title=deck_title).exists():
                self.stdout.write(self.style.WARNING(f"⚠️ Skipped existing deck: {deck_title}"))
                continue

            deck = MemoDeck.objects.create(
                title=deck_title,
                description=f"A deck about {deck_title}.",
                author=user
            )
            deck.tags.set(random.sample(tags, k=min(3, len(tags))))
            self.stdout.write(self.style.SUCCESS(f"📘 Created deck: {deck_title}"))

            for i, (card_title, card_content) in enumerate(cards, start=1):
                MemoCard.objects.create(
                    deck=deck,
                    title=card_title,
                    content=card_content,
                    order=i
                )
            self.stdout.write(self.style.SUCCESS(f"🃏 Added {len(cards)} cards to deck: {deck_title}"))

        self.stdout.write(self.style.SUCCESS("✅ MemoDeck ingress complete."))
