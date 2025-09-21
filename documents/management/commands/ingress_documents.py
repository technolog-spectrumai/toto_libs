from django.contrib.auth.models import User
from lorem_text import lorem
from oya.ingress import IngressCommand
from django.core.files.base import ContentFile
from django.utils.text import slugify

from documents.models import (
    Tag, Department,
    LatexPreset, HTMLPreset,
    Document, DocumentSection, DocumentSubSection,
    HTMLFile
)
from documents.convert import LatexDocumentConverter  # your converter class

class Command(IngressCommand):
    help = "Seed a LaTeX document with tags, department, presets, structured content, and HTML conversion"

    def process(self, _):
        self.create_dashboard_item(
            title="Seeded Document",
            icon="file-text",
            description="LaTeX document with structured sections and HTML preview.",
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
            preamble=r"\usepackage{amsmath}\usepackage{graphicx}\usepackage{hyperref}",
            footer_note="This document is confidential and intended solely for internal use."
        )

        # 🌐 HTML Preset
        html_preset = HTMLPreset.objects.create(
            name="Default Web Style"
        )

        # 📄 Document
        document = Document.objects.create(
            title="Seeded LaTeX Document",
            created_by=users[0],
            department=department,
            preset=latex_preset,
            content="\\section{Introduction} This is the opening paragraph."
        )
        document.tags.set(tags[:3])

        # 🧩 Sections
        section = DocumentSection.objects.create(
            document=document,
            order=1,
            title="Abstract",
            content=lorem.paragraph()
        )

        DocumentSubSection.objects.create(
            section=section,
            order=1,
            title="Details",
            content=lorem.paragraph()
        )

        # 🔁 Convert to HTML and store as HTMLFile
        html_content = LatexDocumentConverter(document).to_html()

        HTMLFile.objects.filter(document=document).delete()

        HTMLFile.objects.create(
            document=document,
            preset=html_preset,
            file=ContentFile(html_content.encode('utf-8'), name=f"{slugify(document.title)}.html")
        )

        self.stdout.write(self.style.SUCCESS("✅ Document ingress completed with HTML conversion."))
