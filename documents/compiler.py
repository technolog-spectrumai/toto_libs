import os
import shutil
import tempfile
from django.conf import settings
from django.utils.text import slugify
from pylatex import NoEscape, Figure, Package, Command, Section, Subsection
from pylatex import Document as PyDocument



class LatexCompiler:
    def __init__(self, document):
        self.document = document
        self.preset = document.preset
        self.output_dir = os.path.join(settings.MEDIA_ROOT, 'compiled_pdfs')
        os.makedirs(self.output_dir, exist_ok=True)

    def _add_image(self, doc, image):
        image_field = getattr(image, "file", None)
        if not image_field:
            return

        image_path = getattr(image_field, "path", None)
        if not image_path or not os.path.exists(image_path):
            return

        safe_dir = tempfile.mkdtemp(dir=self.output_dir)
        ext = os.path.splitext(image_path)[1]
        safe_name = slugify(os.path.basename(image_path).replace(ext, "")) + ext
        safe_path = os.path.join(safe_dir, safe_name)
        shutil.copy(image_path, safe_path)

        with doc.create(Figure(position="h!")) as fig:
            fig.add_image(safe_path, width=NoEscape(r"0.8\textwidth"))
            caption = getattr(image, "caption", None)
            if caption:
                fig.add_caption(caption)

    def _build(self):
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

        # Sections and Subsections
        for section in self.document.sections.all():
            with doc.create(Section(section.title)):
                if section.content:
                    doc.append(NoEscape(section.content))
                for subsection in section.subsections.all():
                    with doc.create(Subsection(subsection.title)):
                        if subsection.content:
                            doc.append(NoEscape(subsection.content))
                        image_obj = getattr(subsection, "image", None)
                        if image_obj:
                            self._add_image(doc, image_obj)

        # Footer note
        if self.preset and self.preset.footer_note:
            doc.append(NoEscape(r'\vfill'))
            doc.append(NoEscape(r'\begin{center}'))
            doc.append(NoEscape(r'\small ' + self.preset.footer_note))
            doc.append(NoEscape(r'\end{center}'))

        return doc

    # def compile(self):
    #     doc = self._build()
    #     file_base = slugify(self.document.title)
    #     filename = f"{file_base}.pdf"
    #     filepath = os.path.join(self.output_dir, filename)
    #     self._compile_pdf(doc, file_base)
    #     return filepath if os.path.exists(filepath) else None
    #
    # def _compile_pdf(self, doc, file_base):
    #     try:
    #         doc.generate_pdf(
    #             os.path.join(self.output_dir, file_base),
    #             clean_tex=True,
    #             compiler="pdflatex",
    #             compiler_args=["-interaction=nonstopmode", "-shell-escape"]
    #         )
    #     except Exception as e:
    #         log_path = os.path.join(self.output_dir, f"{file_base}.log")
    #         if os.path.exists(log_path):
    #             with open(log_path, 'r') as log_file:
    #                 error_log = ""  # log_file.read()
    #             raise RuntimeError(f"LaTeX compilation failed:\n{error_log}")
    #         else:
    #             raise RuntimeError(f"LaTeX compilation failed: {e}")

    def to_tex(self):
        doc = self._build()
        file_base = slugify(self.document.title)
        tex_path = os.path.join(self.output_dir, f"{file_base}")
        doc.generate_tex(tex_path)
        tex_path+= ".tex"
        return tex_path
