import subprocess
from .models import LatexDocument

# ─────────────────────────────────────────────────────────────
# 🔁 Pandoc Conversion Wrapper
# ─────────────────────────────────────────────────────────────
class PandocConverter:
    def __init__(self, from_format: str, to_format: str):
        self.from_format = from_format
        self.to_format = to_format

    def convert(self, content: str) -> str:
        try:
            result = subprocess.run(
                ['pandoc', '--from=' + self.from_format, '--to=' + self.to_format],
                input=content.encode('utf-8'),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True
            )
            return result.stdout.decode('utf-8')
        except subprocess.CalledProcessError as e:
            return self._error_output(e.stderr.decode('utf-8'))

    def _error_output(self, error: str) -> str:
        if self.to_format == 'latex':
            return f"% Pandoc conversion error:\n{error}"
        elif self.to_format == 'html':
            return f"<pre>Pandoc conversion error:\n{error}</pre>"
        else:
            return f"Conversion error:\n{error}"


# ─────────────────────────────────────────────────────────────
# 🧱 Base Converter
# ─────────────────────────────────────────────────────────────
class DocumentConverter:
    def __init__(self, section):
        self.section = section
        self.pandoc = None  # to be defined in subclass

    def convert(self):
        raise NotImplementedError("Subclasses must implement convert()")


# # ─────────────────────────────────────────────────────────────
# # 🌐 HTML → LaTeX Converter
# # ─────────────────────────────────────────────────────────────
# class HTMLToLaTeXConverter(DocumentConverter):
#     def __init__(self, section: HTMLSection):
#         super().__init__(section)
#         self.pandoc = PandocConverter('html', 'latex')
#
#     def convert(self):
#         linked_doc = self.section.document.linked_latex
#         if not linked_doc:
#             return  # No linked LaTeX document to convert into
#
#         # Get or create matching LaTeX section
#         latex_section, _ = LatexSection.objects.get_or_create(
#             document=linked_doc,
#             order=self.section.order,
#             title=self.section.title
#         )
#
#         for html_sub in self.section.subsections.all():
#             latex_content = self.pandoc.convert(html_sub.content)
#             LatexSubSection.objects.create(
#                 section=latex_section,
#                 order=html_sub.order,
#                 title=html_sub.title,
#                 content=latex_content
#             )
#
#         self.section.subsections.all().delete()


# ─────────────────────────────────────────────────────────────
# 📄 LaTeX → HTML Converter
# ─────────────────────────────────────────────────────────────
class LaTeXToHTMLConverter:
    def __init__(self):
        self.pandoc = PandocConverter('latex', 'html')

    def convert_document(self, document: LatexDocument):
        html_parts = [f"<h1>{document.title}</h1>"]

        if document.summary:
            html_parts.append(f"<div class='summary'>{self.pandoc.convert(document.summary)}</div>")

        for section in document.sections.all().order_by('order'):
            html_parts.append(f"<h2>{section.title}</h2>")
            if section.content:
                html_parts.append(self.pandoc.convert(section.content))

            for subsection in section.subsections.all().order_by('order'):
                html_parts.append(f"<h3>{subsection.title}</h3>")
                if subsection.content:
                    html_parts.append(self.pandoc.convert(subsection.content))

        full_html = "\n".join(html_parts)

        return full_html



