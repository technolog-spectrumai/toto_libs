from django.core.management.base import BaseCommand
from django.utils.text import slugify
from django.contrib.auth.models import User
from documents.models import Document, Department, Tag, Section

class Command(BaseCommand):
    help = "Populate the database with sample documents"

    def handle(self, *args, **options):
        # Ensure there's at least one user
        user, _ = User.objects.get_or_create(username='demo_user', defaults={'email': 'demo@example.com'})

        # Create two departments
        dept1, _ = Department.objects.get_or_create(name='Strategy Department', defaults={'owner': user})
        dept2, _ = Department.objects.get_or_create(name='Operations Department', defaults={'owner': user})

        # Sample tags
        tag_names = ['Urgent', 'Finance', 'Strategy', 'Internal']
        tags = [Tag.objects.get_or_create(name=name)[0] for name in tag_names]

        # Sample documents with alternating department assignment
        samples = [
            ('Q3 Analysis', 'Detailed breakdown of Q3 performance.', 'Analysis', dept1),
            ('Infrastructure Plan', 'Roadmap for infrastructure upgrades.', 'Plan', dept2),
            ('Design Guidelines', 'Visual and UX standards for new products.', 'Design', dept1),
            ('Annual Report', 'Comprehensive summary of yearly activities.', 'Report', dept2),
        ]

        section_data = {
            'Q3 Analysis': [
                ('Executive Summary', 'Overview of Q3 performance highlights.'),
                ('Revenue Breakdown', 'Detailed analysis of revenue streams and growth.'),
                ('Challenges & Risks', 'Identified risks and mitigation strategies.'),
            ],
            'Infrastructure Plan': [
                ('Current State Assessment', 'Evaluation of existing infrastructure.'),
                ('Upgrade Roadmap', 'Timeline and scope of planned upgrades.'),
                ('Budget Allocation', 'Projected costs and funding sources.'),
            ],
            'Design Guidelines': [
                ('Brand Identity', 'Core visual elements and tone.'),
                ('UX Principles', 'User experience standards and accessibility.'),
                ('Component Library', 'Reusable UI components and patterns.'),
            ],
            'Annual Report': [
                ('Year in Review', 'Major milestones and achievements.'),
                ('Financial Summary', 'Income, expenses, and net results.'),
                ('Strategic Outlook', 'Goals and initiatives for the coming year.'),
            ]
        }

        for title, summary, doc_type, department in samples:
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
                department=department,
                author=user
            )
            doc.tags.set(tags[:2])  # Assign first two tags
            self.stdout.write(self.style.SUCCESS(f"Created document: {title} in {department.name}"))

            # Add sections if it's a Report
            if title in section_data:
                for i, (heading, content) in enumerate(section_data[title], start=1):
                    Section.objects.create(
                        report=doc,
                        order=i,
                        heading=heading,
                        content=content
                    )
                self.stdout.write(self.style.SUCCESS(f"Added {len(section_data[title])} sections to '{title}'"))

        self.stdout.write(self.style.SUCCESS("Ingress complete."))
