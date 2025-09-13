import os
import tempfile
from django.template.loader import render_to_string
from django.utils.timezone import now
from weasyprint import HTML as WeasyHTML
from pylatex import Document as LaTeXDoc, Section as LaTeXSection, Subsection as LaTeXSubsection, Command
from pylatex.utils import NoEscape
from .models import Document


# ────────────────────────────────────────────────
# 🌐 HTML to PDF Converter
# ────────────────────────────────────────────────

class HTMLToPDFConverter:
    def __init__(self, document: Document):
        self.document = document

    def render_html(self):
        sections = self.document.sections.prefetch_related('subsections').order_by('order') if self.document.deep else []
        context = {
            'document': self.document,
            'sections': sections,
            'generated_at': now(),
            'preset': self.document.preset,
        }
        return render_to_string('documents/pdf.html', context)

    def generate_pdf(self):
        html_content = self.render_html()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as output:
            WeasyHTML(string=html_content).write_pdf(output.name)
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
