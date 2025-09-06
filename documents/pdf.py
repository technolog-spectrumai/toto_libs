import os
import tempfile
from django.conf import settings
from django.template.loader import render_to_string
from django.utils.timezone import now
from weasyprint import HTML
from pylatex import Document as LaTeXDoc, Section, Subsection, Command
from pylatex.utils import NoEscape
from .models import HtmlDocument, LatexDocument
from django.db.models import Prefetch
from .models import HTMLSubSection, LaTeXSubSection


# ─────────────────────────────────────────────────────────────
# 🧱 Base PDF Generator
# ─────────────────────────────────────────────────────────────
class DocumentPDFGenerator:
    def __init__(self, document):
        self.document = document

    def generate(self):
        raise NotImplementedError("Subclasses must implement generate()")


# ─────────────────────────────────────────────────────────────
# 🌐 HTML Document PDF Generator
# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
# 🌐 HTML Document PDF Generator
# ─────────────────────────────────────────────────────────────
class HTMLDocumentPDFGenerator(DocumentPDFGenerator):
    def generate(self):
        seal_path = None
        if self.document.department.seal:
            seal_path = os.path.join(settings.MEDIA_ROOT, self.document.department.seal.name)

        sections = self.document.sections.prefetch_related(
            Prefetch('html_subsections', queryset=HTMLSubSection.objects.select_related('image')),
            Prefetch('latex_subsections')  # Optional, if needed for mixed content
        ).all()

        sections_data = []
        for section in sections:
            section_data = {
                "order": section.order,
                "heading": section.heading,
                "subsections": [
                    {
                        "order": sub.order,
                        "title": sub.title,
                        "created_at": sub.created_at,
                        "image": sub.image,
                        "image_path": os.path.join(settings.MEDIA_ROOT, sub.image.file.name)
                        if sub.image and sub.image.file else None,
                    }
                    for sub in section.html_subsections.all()
                ]
            }
            sections_data.append(section_data)

        notice = '''This document is confidential and intended 
                    solely for the use of the individual or entity to whom it is addressed.
                    Unauthorized distribution, reproduction, or disclosure is strictly prohibited.'''

        html_string = render_to_string("documents/pdf.html", {
            "document": self.document,
            "sections": sections_data,
            "seal_path": seal_path,
            "copyright_holder": "SpectrumAi.pl",
            "copyright_notice": notice,
            "now": now(),
        })

        base_url = settings.MEDIA_ROOT
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as output:
            HTML(string=html_string, base_url=base_url).write_pdf(output.name)
            return output.name


# ─────────────────────────────────────────────────────────────
# 📄 LaTeX Document PDF Generator
# ─────────────────────────────────────────────────────────────
class LaTeXDocumentPDFGenerator(DocumentPDFGenerator):

    @staticmethod
    def create_notice(doc):
        doc.append(NoEscape(r'\begin{center}'))
        doc.append(NoEscape(r'\textbf{Confidential Notice}'))
        doc.append(NoEscape(r'\end{center}'))
        doc.append(NoEscape(
            r'This document is confidential and intended solely for the use of the individual '
            r'or entity to whom it is addressed. Unauthorized distribution, '
            r'reproduction, or disclosure is strictly prohibited.'))
        doc.append(NoEscape(r'\vspace{1cm}'))

    def generate(self):
        doc = LaTeXDoc()

        # Preamble setup
        doc.preamble.append(Command('title', self.document.title))
        doc.preamble.append(Command('author', self.document.author.get_full_name()))
        doc.preamble.append(Command('date', NoEscape(r'\today')))
        doc.append(NoEscape(r'\maketitle'))

        # Sections and subsections
        sections = self.document.sections.prefetch_related('latex_subsections').all()
        for section in sections:
            with doc.create(Section(section.heading)):
                for sub in section.latex_subsections.all():
                    with doc.create(Subsection(f"{sub.order}. {sub.title}")):
                        doc.append(NoEscape(sub.content))

        self.create_notice(doc)
        # Generate PDF to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as output:
            doc.generate_pdf(output.name, clean_tex=False)
            return output.name + ".pdf"


# ─────────────────────────────────────────────────────────────
# 🔀 Dispatcher
# ─────────────────────────────────────────────────────────────
def generate_document_pdf(document):
    if isinstance(document, LatexDocument):
        generator = LaTeXDocumentPDFGenerator(document)
    elif isinstance(document, HtmlDocument):
        generator = HTMLDocumentPDFGenerator(document)
    else:
        raise ValueError("Unsupported document type")

    return generator.generate()