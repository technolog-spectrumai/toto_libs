import os
from .models import LatexPreset
from pylatex import Document as LatexDoc, Section, Subsection, Command, Package
from pylatex.utils import NoEscape
import tempfile


class LaTeXToPDFConverter:

    def __init__(self, document):
        self.document = document

    def _build_document(self):
        preset = self.document.latex_preset
        doc = LatexDoc(documentclass=preset.document_class)

        for pkg in preset.packages:
            doc.packages.append(Package(pkg))

        if preset.preamble:
            doc.preamble.append(NoEscape(preset.preamble))

        doc.preamble.append(Command('title', self.document.title))
        author = self.document.department.owner.get_full_name() if self.document.department else "Unknown"
        doc.preamble.append(Command('author', author))
        doc.preamble.append(Command('date', NoEscape(r'\today')))
        doc.append(NoEscape(r'\maketitle'))

        if self.document.summary:
            doc.append(NoEscape(r'\begin{abstract}'))
            doc.append(self.document.summary)
            doc.append(NoEscape(r'\end{abstract}'))

        if self.document.deep:
            for section in self.document.sections.all():
                with doc.create(Section(section.title)):
                    if section.latex:
                        doc.append(section.latex)
                    if section.deep:
                        for subsection in section.subsections.all():
                            with doc.create(Subsection(subsection.title)):
                                if subsection.latex:
                                    doc.append(subsection.latex)

        if preset.footer_note:
            doc.append(NoEscape(r'\vfill'))
            doc.append(NoEscape(r'\begin{center}'))
            doc.append(NoEscape(r'\textit{' + preset.footer_note + '}'))
            doc.append(NoEscape(r'\end{center}'))
        return doc

    def generate_pdf(self):
        doc = self._build_document()

        ext = ".pdf"
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as temp_file:
            output_path = temp_file.name

        # Generate the PDF directly to the temp file path
        doc.generate_pdf(filepath=output_path.replace(ext, ""), clean_tex=False, compiler='pdflatex')

        # Confirm the file exists and return its path
        if os.path.exists(output_path):
            return output_path
        else:
            raise FileNotFoundError(f"PDF generation failed at {output_path}")


