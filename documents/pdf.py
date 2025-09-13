import os
import tempfile
from django.template.loader import render_to_string
from django.utils.timezone import now
from weasyprint import HTML as WeasyHTML
from .models import Document, LatexPreset
from django.conf import settings
from pylatex import Document as LatexDoc, Section, Subsection, Command, Package
from pylatex.utils import NoEscape
from io import BytesIO
from abc import ABC, abstractmethod
from .models import Document


class BasePDFConverter(ABC):
    def __init__(self, document: Document):
        if not isinstance(document, Document):
            raise TypeError("Expected a Document instance.")
        self.document = document

    @abstractmethod
    def generate_pdf(self):
        """Generate a PDF and return it (path or bytes)."""
        pass


# ────────────────────────────────────────────────
# 🌐 HTML to PDF Converter
# ────────────────────────────────────────────────

class HTMLToPDFConverter(BasePDFConverter):

    def generate_pdf(self):
        # Department seal
        seal_path = None
        if self.document.department and self.document.department.seal:
            seal_path = os.path.join(settings.MEDIA_ROOT, self.document.department.seal.name)

        # Sections and subsections
        sections = self.document.sections.prefetch_related('subsections').order_by('order') if self.document.deep else []

        structured_sections = []
        for section in sections:
            subsections = section.subsections.all().order_by('order') if section.deep else []
            structured_sections.append({
                "order": section.order + 1,
                "heading": section.title,
                "content": section.content if not section.deep else None,
                "all_subsections": [
                    {
                        "order": f"{section.order + 1}.{sub.order + 1}",
                        "title": sub.title,
                        "content": sub.content
                    }
                    for sub in subsections
                ]
            })

        # Legal metadata from department
        department = self.document.department
        copyright_holder = (
            department and department.copyright_holder
            or "SpectrumAi.pl"
        )
        copyright_notice = (
            department and department.copyright_notice
            or "This document is confidential and intended solely for the use of the individual or entity to whom it is addressed. "
               "Unauthorized distribution, reproduction, or disclosure is strictly prohibited."
        )

        # Render HTML
        html_string = render_to_string("documents/pdf.html", {
            "document": self.document,
            "sections_prefetched": structured_sections,
            "seal_path": seal_path,
            "copyright_holder": copyright_holder,
            "copyright_notice": copyright_notice,
            "now": now(),
        })

        # Generate PDF
        base_url = settings.MEDIA_ROOT
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as output:
            WeasyHTML(string=html_string, base_url=base_url).write_pdf(output.name)
            return output.name


class LaTeXToPDFConverter(BasePDFConverter):

    def __init__(self, document):
        if not document.engine == 'latex':
            raise ValueError("Document engine must be LaTeX.")
        if not isinstance(document.preset, LatexPreset):
            raise TypeError("Preset must be a LatexPreset instance.")
        super().__init__(document)
        self.preset = document.preset

    def _build_document(self):
        doc = LatexDoc(documentclass=self.preset.document_class)

        for pkg in self.preset.packages:
            doc.packages.append(Package(pkg))
        #
        # if self.preset.preamble:
        #     doc.preamble.append(NoEscape(self.preset.preamble))
        #
        doc.preamble.append(Command('title', self.document.title))
        author = self.document.department.owner.get_full_name() if self.document.department else "Unknown"
        doc.preamble.append(Command('author', author))
        doc.preamble.append(Command('date', NoEscape(r'\today')))
        doc.append(NoEscape(r'\maketitle'))

        if self.document.summary:
            doc.append(NoEscape(r'\begin{abstract}'))
            doc.append(self.document.summary)
            doc.append(NoEscape(r'\end{abstract}'))

        if self.document.content:
            with doc.create(Section(self.document.title)):
                doc.append(self.document.content)

        if self.document.deep:
            for section in self.document.sections.all():
                with doc.create(Section(section.title)):
                    if section.content:
                        doc.append(section.content)
                    if section.deep:
                        for subsection in section.subsections.all():
                            with doc.create(Subsection(subsection.title)):
                                if subsection.content:
                                    doc.append(subsection.content)

        if self.preset.footer_note:
            doc.append(NoEscape(r'\vfill'))
            doc.append(NoEscape(r'\begin{center}'))
            doc.append(NoEscape(r'\textit{' + self.preset.footer_note + '}'))
            doc.append(NoEscape(r'\end{center}'))

        return doc

    def generate_pdf(self):
        doc = self._build_document()
        filename = f"{self.document.slug}.pdf"
        output_path = os.path.join(os.getcwd(), filename)
        doc.generate_pdf(filepath=self.document.slug, clean_tex=False, compiler='pdflatex')

        # Return the path to the generated PDF
        if os.path.exists(output_path):
            return output_path
        else:
            raise FileNotFoundError(f"PDF generation failed. {output_path}")


