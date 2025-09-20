from django.contrib.auth.models import User
from lorem_text import lorem
from oya.ingress import IngressCommand
from documents.models import (
    Tag, Department,
    HTMLPreset, LatexPreset,
    HTMLDocument, LatexDocument,
    HtmlDocumentEditor, LatexDocumentEditor,
    EditorSection, EditorSubSection
)

class Command(IngressCommand):
    help = "Seed HTML and LaTeX editors linked to empty documents, with presets, departments, tags, and structured content"

    def process(self, _):
        self.create_dashboard_item(
            title="Document Editors",
            icon="edit",
            description="HTML and LaTeX editors linked to empty documents, with structured sections and lorem content.",
            link="/documents/"
        )

        self.stdout.write("🚀 Starting editor ingress...")

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

        # 🌐 HTML Preset
        html_preset = HTMLPreset.objects.create(
            name="Default Web Style",
            description="Standard HTML styling for web editors."
        )

        # 🧪 LaTeX Preset
        latex_preset = LatexPreset.objects.create(
            name="Academic Article",
            description="LaTeX preset for academic editors.",
            document_class="article",
            packages=["amsmath", "graphicx", "hyperref"],
            preamble=r"\usepackage{amsmath}\usepackage{graphicx}\usepackage{hyperref}"
        )

        # 📄 Empty HTML Document
        html_doc = HTMLDocument.objects.create(
            title="Empty HTML Document",
            created_by=users[0],
            department=department,
            preset=html_preset,
            content=""
        )
        html_doc.tags.set(tags[:2])

        # 📄 Empty LaTeX Document
        latex_doc = LatexDocument.objects.create(
            title="Empty LaTeX Document",
            created_by=users[0],
            department=department,
            content=""
        )
        latex_doc.tags.set(tags[2:])

        # 🧑‍💻 HTML Editor
        html_editor = HtmlDocumentEditor.objects.create(
            title="HTML Editor Seed",
            created_by=users[0],
            department=department,
            document=html_doc,
            summary=lorem.sentence()
        )

        # 🧑‍💻 LaTeX Editor
        latex_editor = LatexDocumentEditor.objects.create(
            title="LaTeX Editor Seed",
            created_by=users[0],
            department=department,
            document=latex_doc,
            preset=latex_preset,
            summary=lorem.sentence()
        )

        # 🧩 HTML Sections
        html_section = EditorSection.objects.create(
            document=html_editor,
            order=1,
            title="HTML Introduction",
            content=f"<h1>Intro</h1><p>{lorem.paragraph()}</p>"
        )
        EditorSubSection.objects.create(
            section=html_section,
            order=1,
            title="HTML Subsection A",
            content=f"<p>{lorem.paragraph()}</p>"
        )

        # 🧩 LaTeX Sections
        latex_section = EditorSection.objects.create(
            document=latex_editor,
            order=1,
            title="LaTeX Abstract",
            content=f"\\section{{Abstract}} {lorem.paragraph()}"
        )
        EditorSubSection.objects.create(
            section=latex_section,
            order=1,
            title="LaTeX Subsection B",
            content=f"\\subsection{{Details}} {lorem.paragraph()}"
        )

        self.stdout.write(self.style.SUCCESS("✅ Editor ingress completed successfully."))
