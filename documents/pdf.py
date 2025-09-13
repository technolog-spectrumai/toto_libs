import os
import tempfile
from django.template.loader import render_to_string
from django.utils.timezone import now
from weasyprint import HTML as WeasyHTML
from pylatex import Document as LaTeXDoc, Section as LaTeXSection, Subsection as LaTeXSubsection, Command
from pylatex.utils import NoEscape
from .models import Document
from django.conf import settings


# ────────────────────────────────────────────────
# 🌐 HTML to PDF Converter
# ────────────────────────────────────────────────

class HTMLToPDFConverter:
    def __init__(self, document: Document):
        self.document = document

    def generate_pdf(self):

        # Sections and subsections
        sections = self.document.sections.prefetch_related('subsections').order_by('order') if self.document.deep else []

        structured_sections = []
        for section in sections:
            subsections = section.subsections.all().order_by('order') if section.deep else []
            structured_sections.append({
                "order": section.order+1,
                "heading": section.title,
                "content": section.content if not section.deep else None,
                "all_subsections": [
                    {
                        "order": f"{section.order+1}.{sub.order+1}",
                        "title": sub.title,
                        "content": sub.content
                    }
                    for sub in subsections
                ]
            })

        # Confidential notice
        notice = (
            "This document is confidential and intended solely for the use of the individual or entity to whom it is addressed. "
            "Unauthorized distribution, reproduction, or disclosure is strictly prohibited."
        )

        # Render HTML
        html_string = render_to_string("documents/pdf.html", {
            "document": self.document,
            "sections_prefetched": structured_sections,
            "copyright_holder": "SpectrumAi.pl",
            "copyright_notice": notice,
            "now": now(),
        })

        # Generate PDF
        base_url = settings.MEDIA_ROOT
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as output:
            WeasyHTML(string=html_string, base_url=base_url).write_pdf(output.name)
            return output.name


# ────────────────────────────────────────────────
# 🧪 LaTeX to PDF Converter
# ────────────────────────────────────────────────

class LaTeXToPDFConverter:
    def __init__(self, document: Document):
        self.document = document

    def build_latex(self):
        preset = self.document.preset
        doc = LaTeXDoc(documentclass=NoEscape(preset.document_class if hasattr(preset, 'document_class') else 'article'))

        doc.preamble.append(Command('title', self.document.title))
        doc.preamble.append(Command('date', NoEscape(r'\today')))
        doc.append(NoEscape(r'\maketitle'))

        if self.document.deep:
            sections = self.document.sections.prefetch_relatead('subsections').order_by('order')
            for section in sections:
                sec = LaTeXSection(section.title)
                if section.deep:
                    for sub in section.subsections.all():
                        subsec = LaTeXSubsection(sub.title)
                        if sub.content:
                            subsec.append(NoEscape(sub.content))
                        sec.append(subsec)
                else:
                    if section.content:
                        sec.append(NoEscape(section.content))
                doc.append(sec)
        else:
            if self.document.content:
                doc.append(NoEscape(self.document.content))

        return doc

    def generate_pdf(self):
        latex_doc = self.build_latex()
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = os.path.join(tmpdir, f"{self.document.slug}.pdf")
            latex_doc.generate_pdf(pdf_path, clean=True, silent=True)
            return pdf_path
