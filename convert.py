import subprocess
from .models import LatexSection, LatexSubSection

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
#         for html_sub in self.section.subsections.all():
#             latex_content = self.pandoc.convert(html_sub.content)
#             LatexSubSection.objects.create(
#                 section=self._get_latex_section(),
#                 order=html_sub.order,
#                 title=html_sub.title,
#                 content=latex_content
#             )
#         self.section.subsections.all().delete()
#
#     def _get_latex_section(self):
#         # Assumes a corresponding LatexSection exists or is created elsewhere
#         return LatexSection.objects.get_or_create(
#             document=self.section.document.linked_latex,
#             order=self.section.order,
#             title=self.section.title
#         )[0]


# ─────────────────────────────────────────────────────────────
# 📄 LaTeX → HTML Converter
# ─────────────────────────────────────────────────────────────
class LaTeXToHTMLConverter(DocumentConverter):
    def __init__(self, section: LatexSection):
        super().__init__(section)
        self.pandoc = PandocConverter('latex', 'html')

    def convert(self):
        for latex_sub in self.section.subsections.all():
            html_content = self.pandoc.convert(latex_sub.content)
            HTMLSubSection.objects.create(
                section=self._get_html_section(),
                order=latex_sub.order,
                title=latex_sub.title,
                content=html_content
            )
        self.section.subsections.all().delete()

    def _get_html_section(self):
        # Assumes a corresponding HTMLSection exists or is created elsewhere
        return HTMLSection.objects.get_or_create(
            document=self.section.document.linked_html_versions.first(),
            order=self.section.order,
            title=self.section.title
        )[0]
