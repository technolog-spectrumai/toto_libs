from django.utils import timezone
from django.contrib.auth.models import User
from lorem_text import lorem
from oya.ingress import IngressCommand
from documents.models import (
    Tag, Department,
    HTMLPreset, LatexPreset,
    LatexDocument, HTMLDocument,
    HtmlDocumentEditor, LatexDocumentEditor,
    EditorSection, EditorSubSection
)

class Command(IngressCommand):
    help = "Seed LaTeX and HTML documents with editors, presets, departments, and tags"

    def process(self, _):
        self.create_dashboard_item(
            title="Documents",
            icon="file-text",
            description="LaTeX and HTML documents with presets, departments, tags, and structured editors.",
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

        # 🌐 HTML Preset
        html_preset = HTMLPreset.objects.create(
            name="Default Web Style",
            description="Standard HTML styling for web documents."
        )

        # 🧪 LaTeX Preset
        latex_preset = LatexPreset.objects.create(
            name="Academic Article",
            description="LaTeX preset for academic papers.",
            document_class="article",
            packages=["amsmath", "graphicx", "hyperref"],
            preamble=r"\usepackage{amsmath}\usepackage{graphicx}\usepackage{hyperref}"
        )

        # 📄 HTML Document
        html_doc = HTMLDocument.objects.create(
            title="HTML Sample Document",
            created_by=users[0],
            department=department,
            preset=html_preset,
            content="<h1>Welcome</h1><p>This is an HTML document.</p>"
        )
        html_doc.tags.set(tags[:2])

        # 📄 LaTeX Document
        latex_doc = LatexDocument.objects.create(
            title="LaTeX Sample Document",
            created_by=users[0],
            department=department,
            content=r"\section{Introduction}\nThis is a LaTeX document."
        )
        latex_doc.tags.set(tags[2:])

        # 🧑‍💻 HTML Editor
        html_editor = HtmlDocumentEditor.objects.create(
            title="HTML Editor",
            created_by=users[0],
            department=department,
            document=html_doc,
            summary="Editor for HTML content."
        )

        # 🧑‍💻 LaTeX Editor
        latex_editor = LatexDocumentEditor.objects.create(
            title="LaTeX Editor",
            created_by=users[0],
            department=department,
            document=latex_doc,
            preset=latex_preset,
            summary="Editor for LaTeX content."
        )

        # 🧩 Editor Sections
        html_section = EditorSection.objects.create(
            document=html_editor,
            order=1,
            title="Introduction",
            content="<h1>Intro</h1><p>HTML intro section.</p>"
        )
        EditorSubSection.objects.create(
            section=html_section,
            order=1,
            title="Subsection A",
            content="<p>Details about subsection A.</p>"
        )

        latex_section = EditorSection.objects.create(
            document=latex_editor,
            order=1,
            title="Abstract",
            content=r"\section{Abstract}\nThis is the abstract."
        )
        EditorSubSection.objects.create(
            section=latex_section,
            order=1,
            title="Subsection B",
            content=r"\subsection{Details}\nMore LaTeX content here."
        )

        self.stdout.write(self.style.SUCCESS("✅ Document ingress completed successfully."))
