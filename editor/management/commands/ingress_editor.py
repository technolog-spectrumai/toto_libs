from django.contrib.auth.models import User
from lorem_text import lorem
from oya.ingress import IngressCommand
from django.utils.text import slugify

from editor.models import (
    Tag, Department,
    LatexPreset,
    Document, DocumentSection, DocumentSubSection
)
from editor.convert import LatexToHTMLConverter


class Command(IngressCommand):
    help = "Seed a LaTeX document with tags, department, presets, structured content, and in-place HTML conversion"

    def process(self, _):
        self.create_dashboard_item(
            title="Seeded Document",
            icon="file-text",
            description="LaTeX document with structured sections and HTML conversion.",
            link="/documents/"
        )

        self.stdout.write("🚀 Starting document ingress...")

        users = list(User.objects.all())
        if not users:
            self.stdout.write(self.style.ERROR("❌ No users found. Create at least one user before running ingress."))
            return

        # 📌 Tags
        tag_names = ["Confidential", "Draft", "Final", "Research", "Internal"]
        tags = [Tag.objects.get_or_create(name=name)[0] for name in tag_names]

        # 🏢 Department
        department = Department.objects.create(
            name="Documentation & Research",
            owner=users[0]
        )

        # 🧪 LaTeX Preset
        latex_preset = LatexPreset.objects.create(
            name="Academic Article",
            document_class="article",
            packages=["amsmath", "graphicx", "hyperref"],
            preamble=r"",
            footer_note="This document is confidential and intended solely for internal use."
        )

        # 📄 Document
        document = Document.objects.create(
            title="Seeded LaTeX Document",
            created_by=users[0],
            department=department,
            preset=latex_preset
        )
        document.tags.set(tags[:3])

        # 🧩 Sections and Subsections
        sections = [
            ("Abstract", lorem.paragraph()),
            ("Methodology", f"{lorem.paragraph()}\n\n{lorem.paragraph()}"),
            ("Results", f"{lorem.paragraph()}\n\n{lorem.paragraph()}"),
            ("Conclusion", lorem.paragraph())
        ]

        for i, (title, content) in enumerate(sections, start=1):
            section = DocumentSection.objects.create(
                document=document,
                order=i,
                title=title,
                content=content
            )
            for j in range(1, 3):
                DocumentSubSection.objects.create(
                    section=section,
                    order=j,
                    title=f"{title} Subsection {j}",
                    content=f"{lorem.paragraph()}\n\n{lorem.paragraph()}"
                )

        self.stdout.write(self.style.SUCCESS("✅ Document ingress completed with HTML conversion and preset assignment."))
