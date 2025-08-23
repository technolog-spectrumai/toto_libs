from django.core.management.base import BaseCommand
from django.utils.text import slugify
from django.contrib.auth.models import User
from documents.models import Document, Office, Tag

class Command(BaseCommand):
    help = "Populate the database with sample documents"

    def handle(self, *args, **options):
        # Ensure there's at least one user and one office
        user, _ = User.objects.get_or_create(username='demo_user', defaults={'email': 'demo@example.com'})
        office, _ = Office.objects.get_or_create(name='Demo Office', defaults={'owner': user})

        # Sample tags
        tag_names = ['Urgent', 'Finance', 'Strategy', 'Internal']
        tags = [Tag.objects.get_or_create(name=name)[0] for name in tag_names]

        # Sample documents
        samples = [
            ('Q3 Analysis', 'Detailed breakdown of Q3 performance.', 'Analysis'),
            ('Infrastructure Plan', 'Roadmap for infrastructure upgrades.', 'Plan'),
            ('Design Guidelines', 'Visual and UX standards for new products.', 'Design'),
            ('Annual Report', 'Comprehensive summary of yearly activities.', 'Report'),
        ]

        for title, summary, doc_type in samples:
            slug = slugify(title)
            if Document.objects.filter(slug=slug).exists():
                self.stdout.write(self.style.WARNING(f"Skipped existing document: {title}"))
                continue

            doc = Document.objects.create(
                slug=slug,
                title=title,
                summary=summary,
                type=doc_type,
                status='Draft',
                office=office,
                author=user
            )
            doc.tags.set(tags[:2])  # Assign first two tags
            self.stdout.write(self.style.SUCCESS(f"Created document: {title}"))

        self.stdout.write(self.style.SUCCESS("Ingress complete."))
