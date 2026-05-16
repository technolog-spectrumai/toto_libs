from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from faker.utils.text import slugify
from toto.memo.models import MemoDeck, MemoCard, Tag, MemoDiagram
from toto.ingress import IngressCommand
from toto.vault.models import Bucket, VaultFile
import random


class Command(IngressCommand):
    help = "Populate the database with sample MemoDecks, MemoCards, Tags, and SVG diagrams"

    # ────────────────────────────────────────────────
    # MAIN PROCESS
    # ────────────────────────────────────────────────

    def process(self):
        # Dashboard entry for deck list

        if not self.full:
            return

        user = self.get_or_create_demo_user()
        tags = self.get_or_create_tags()
        diagrams = self.get_or_create_diagrams(user)
        decks = self.get_sample_decks()

        for deck_title, cards in decks.items():
            if MemoDeck.objects.filter(title=deck_title).exists():
                self.stdout.write(self.style.WARNING(f"⚠️ Skipped existing deck: {deck_title}"))
                continue

            deck = self.create_deck(deck_title, user, tags)
            self.create_cards(deck, cards, diagrams)

        self.stdout.write(self.style.SUCCESS("✅ MemoDeck ingress complete."))

    # ────────────────────────────────────────────────
    # USERS & TAGS
    # ────────────────────────────────────────────────

    def get_or_create_demo_user(self):
        user, _ = User.objects.get_or_create(
            username="demo_user",
            defaults={"email": "demo@example.com"}
        )
        return user

    def get_or_create_tags(self):
        tag_names = [
            "django", "python", "study", "backend",
            "frontend", "devtools", "testing"
        ]
        return [Tag.objects.get_or_create(name=name)[0] for name in tag_names]

    # ────────────────────────────────────────────────
    # SAMPLE SVG DIAGRAMS
    # ────────────────────────────────────────────────

    def get_or_create_diagrams(self, user):
        bucket, _ = Bucket.objects.get_or_create(
            name="Memo Diagrams",
            owner=user,
            defaults={"slug": "memo-diagrams"}
        )

        samples = [
            (
                "Simple Flow",
                "Basic SVG flow diagram",
                "simple-flow",
                """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 160">
  <rect x="20" y="45" width="120" height="70" rx="8" fill="#dbeafe" stroke="#2563eb" stroke-width="3"/>
  <rect x="190" y="45" width="120" height="70" rx="8" fill="#dcfce7" stroke="#16a34a" stroke-width="3"/>
  <rect x="360" y="45" width="120" height="70" rx="8" fill="#fee2e2" stroke="#dc2626" stroke-width="3"/>
  <path d="M140 80 H190 M310 80 H360" stroke="#111827" stroke-width="4" fill="none"/>
  <text x="80" y="87" text-anchor="middle" font-size="20">Start</text>
  <text x="250" y="87" text-anchor="middle" font-size="20">Learn</text>
  <text x="420" y="87" text-anchor="middle" font-size="20">Recall</text>
</svg>"""
            ),
            (
                "Decision Diagram",
                "Basic decision SVG diagram",
                "decision-diagram",
                """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 220">
  <path d="M250 20 L360 95 L250 170 L140 95 Z" fill="#fef3c7" stroke="#d97706" stroke-width="3"/>
  <rect x="30" y="150" width="130" height="50" rx="8" fill="#dbeafe" stroke="#2563eb" stroke-width="3"/>
  <rect x="340" y="150" width="130" height="50" rx="8" fill="#dcfce7" stroke="#16a34a" stroke-width="3"/>
  <path d="M190 130 L150 160 M310 130 L350 160" stroke="#111827" stroke-width="4" fill="none"/>
  <text x="250" y="102" text-anchor="middle" font-size="18">Know it?</text>
  <text x="95" y="182" text-anchor="middle" font-size="18">Review</text>
  <text x="405" y="182" text-anchor="middle" font-size="18">Advance</text>
</svg>"""
            ),
        ]

        diagrams = []
        for title, desc, key, svg in samples:
            vault_file, created = VaultFile.objects.get_or_create(
                bucket=bucket,
                key=key,
                defaults={
                    "owner": user,
                    "title": title,
                    "file_type": "svg",
                    "is_public": True,
                    "notes": desc,
                }
            )
            if created or not vault_file.file:
                vault_file.file.save(f"{key}.svg", ContentFile(svg.encode("utf-8")), save=False)
                vault_file.content_hash = vault_file.create_hash()

            vault_file.file_type = "svg"
            vault_file.is_public = True
            vault_file.save()

            diagram, _ = MemoDiagram.objects.get_or_create(
                title=title,
                defaults={"description": desc, "svg_file": vault_file}
            )
            diagrams.append(diagram)

        return diagrams

    # ────────────────────────────────────────────────
    # SAMPLE DECKS & CARDS
    # ────────────────────────────────────────────────

    def get_sample_decks(self):
        return {
            "Django Basics": [
                ("What is Django?", "Django is a high-level Python web framework."),
                ("What is a QuerySet?", "A QuerySet is a collection of database queries."),
                ("What is a model?", "A model is a Python class that maps to a database table.")
            ],
            "Python Tricks": [
                ("List Comprehensions", "Concise way to create lists."),
                ("Decorators", "Functions that modify other functions."),
                ("Generators", "Functions that yield values lazily.")
            ],
            "Testing Strategies": [
                ("Unit Testing", "Testing individual units of code."),
                ("Integration Testing", "Testing combined parts of a system."),
                ("Mocking", "Simulating parts of code for isolated testing.")
            ]
        }

    def create_deck(self, title, user, tags):
        deck = MemoDeck.objects.create(
            title=title,
            description=f"A deck about {title}.",
            author=user,
            slug=slugify(title)
        )
        deck.tags.set(random.sample(tags, k=min(3, len(tags))))

        self.stdout.write(self.style.SUCCESS(f"📘 Created deck: {title}"))
        return deck

    def create_cards(self, deck, cards, diagrams):
        for i, (card_title, card_content) in enumerate(cards, start=1):

            diagram = random.choice(diagrams) if random.random() < 0.5 else None

            MemoCard.objects.create(
                deck=deck,
                title=card_title,
                content=card_content,
                order=i,
                diagram=diagram
            )

        self.stdout.write(
            self.style.SUCCESS(f"🃏 Added {len(cards)} cards to deck: {deck.title}")
        )
