import os
from pylatex import Document as PyDocument, Section, Subsection, Command, Package
from pylatex.utils import NoEscape
from django.conf import settings
from django.utils.text import slugify


class LatexCompiler:
    def __init__(self, document):
        self.document = document
        self.preset = document.preset
        self.output_dir = os.path.join(settings.MEDIA_ROOT, 'compiled_pdfs')
        os.makedirs(self.output_dir, exist_ok=True)

    def generate_pdf(self):
        doc = PyDocument(documentclass=self.preset.document_class if self.preset else 'article')

        # Add packages
        if self.preset and self.preset.packages:
            for pkg in self.preset.packages:
                doc.packages.append(Package(pkg))

        # Add preamble
        if self.preset and self.preset.preamble:
            doc.preamble.append(NoEscape(self.preset.preamble))

        # Title
        doc.preamble.append(Command('title', self.document.title))
        doc.preamble.append(Command('author', self.document.created_by.get_full_name() or self.document.created_by.username))
        doc.preamble.append(Command('date', NoEscape(r'\today')))
        doc.append(NoEscape(r'\maketitle'))

        # Main content
        if self.document.content:
            doc.append(NoEscape(self.document.content))

        # Sections and Subsections
        for section in self.document.sections.all():
            with doc.create(Section(section.title)):
                if section.content:
                    doc.append(NoEscape(section.content))
                for subsection in section.subsections.all():
                    with doc.create(Subsection(subsection.title)):
                        if subsection.content:
                            doc.append(NoEscape(subsection.content))

        # Footer note
        if self.preset and self.preset.footer_note:
            doc.append(NoEscape(r'\vfill'))
            doc.append(NoEscape(r'\begin{center}'))
            doc.append(NoEscape(r'\small ' + self.preset.footer_note))
            doc.append(NoEscape(r'\end{center}'))

        # Compile
        filename = f"{slugify(self.document.title)}.pdf"
        filepath = os.path.join(self.output_dir, filename)
        doc.generate_pdf(filepath, clean_tex=False, compiler='pdflatex')

        return filepath if os.path.exists(filepath) else None
