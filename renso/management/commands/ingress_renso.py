from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from renso.models import MemoDeck, MemoCard, InfoTag
import random


class Command(BaseCommand):
    help = 'Builds sample MemoDecks, MemoCards, and InfoTags for testing or demo purposes'

    def handle(self, *args, **kwargs):
        user = User.objects.first()
        if not user:
            self.stdout.write(self.style.ERROR("No users found. Please create a user first."))
            return

        tag_names = ['django', 'python', 'study', 'backend', 'frontend', 'devtools', 'testing']
        tags = []

        for name in tag_names:
            tag, created = InfoTag.objects.get_or_create(name=name)
            tags.append(tag)
            if created:
                self.stdout.write(self.style.SUCCESS(f"Created tag: {name}"))

        deck_titles = ['Django Basics', 'Python Tricks', 'Testing Strategies']
        sample_cards = [
            ('What is Django?', 'Django is a high-level Python web framework.'),
            ('What is a QuerySet?', 'A QuerySet is a collection of database queries.'),
            ('What is a model?', 'A model is a Python class that maps to a database table.')
        ]

        for title in deck_titles:
            deck = MemoDeck.objects.create(
                title=title,
                description=f"A deck about {title}.",
                author=user
            )
            selected_tags = random.sample(tags, k=3)
            deck.tags.set(selected_tags)
            deck.save()
            self.stdout.write(self.style.SUCCESS(f"Created deck: {deck.title} with tags: {', '.join(t.name for t in selected_tags)}"))

            for i, (card_title, card_content) in enumerate(sample_cards):
                MemoCard.objects.create(
                    deck=deck,
                    title=card_title,
                    content=card_content,
                    mermaid_code="",
                    order=i  # Assign order based on position
                )
            self.stdout.write(self.style.SUCCESS(f"Added {len(sample_cards)} cards to deck: {deck.title}"))

        self.stdout.write(self.style.SUCCESS("✅ Sample decks, cards, and tags created successfully!"))
