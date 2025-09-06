from django.utils.text import slugify
from django.contrib.auth.models import User
from documents.models import (
    HtmlDocument, Department, Tag, Section,
    HTMLSubSection  # Assuming we're only creating HTML documents here
)
from oya.ingress import IngressCommand


class Command(IngressCommand):
    help = "Populate the database with sample HTML documents and nested sections"

    def process(self, _):
        self.create_dashboard_item(
            title="Documents",
            icon="file-alt",
            description="Documents with nested sections and tags.",
            link="/documents/"
        )

        user, _ = User.objects.get_or_create(
            username='demo_user',
            defaults={'email': 'demo@example.com'}
        )

        dept1, _ = Department.objects.get_or_create(name='Strategy Department', defaults={'owner': user})
        dept2, _ = Department.objects.get_or_create(name='Operations Department', defaults={'owner': user})

        tag_names = ['Urgent', 'Finance', 'Strategy', 'Internal']
        tags = [Tag.objects.get_or_create(name=name)[0] for name in tag_names]

        samples = [
            ('Q3 Analysis', 'Detailed breakdown of Q3 performance.', dept1),
            ('Infrastructure Plan', 'Roadmap for infrastructure upgrades.', dept2),
            ('Design Guidelines', 'Visual and UX standards for new products.', dept1),
            ('Annual Report', 'Comprehensive summary of yearly activities.', dept2),
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

        subsection_template = [
            ('Details', 'Expanded explanation and supporting data.'),
            ('Notes', 'Additional commentary or observations.'),
        ]

        for title, summary, department in samples:
            slug = slugify(title)
            if HtmlDocument.objects.filter(slug=slug).exists():
                self.stdout.write(self.style.WARNING(f"⚠️ Skipped existing document: {title}"))
                continue

            doc = HtmlDocument.objects.create(
                slug=slug,
                title=title,
                summary=summary,
                status='Draft',
                department=department,
                author=user
            )
            doc.tags.set(tags[:2])
            self.stdout.write(self.style.SUCCESS(f"📄 Created HTML document: {title} in {department.name}"))

            if title in section_data:
                for i, (heading, content) in enumerate(section_data[title], start=1):
                    section = Section.objects.create(
                        document=doc,
                        order=i,
                        heading=heading
                    )
                    for j, (sub_title, sub_content) in enumerate(subsection_template, start=1):
                        HTMLSubSection.objects.create(
                            section=section,
                            order=j,
                            title=sub_title,
                            content=f"{sub_content} (for {heading})"
                        )
                self.stdout.write(self.style.SUCCESS(f"🧩 Added {len(section_data[title])} sections and subsections to {title}"))

        self.stdout.write(self.style.SUCCESS("✅ Document ingress complete."))
