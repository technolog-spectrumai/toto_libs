import subprocess
from .models import LatexDocument, HTMLDocument
from pylatex import Document as LatexDoc, Command, Package
from pylatex.utils import NoEscape


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


class LatexDocumentConverter:
    def __init__(self, document: LatexDocument):
        if not isinstance(document, LatexDocument):
            raise TypeError(f"Expected LatexDocument, got {type(document)}")
        self.document = document

    def to_html(self) -> str:
        converter = PandocConverter(from_format='latex', to_format='html')
        return converter.convert(self.document.content)


class HTMLDocumentConverter:
    def __init__(self, document: HTMLDocument):
        if not isinstance(document, HTMLDocument):
            raise TypeError(f"Expected HTMLDocument, got {type(document)}")
        self.document = document

    def to_latex(self) -> str:
        body = self._convert_body()
        return self._build_document(body).dumps()

    def _convert_body(self) -> str:
        converter = PandocConverter(from_format='html', to_format='latex')
        return converter.convert(self.document.content or "")

    def _build_document(self, body: str) -> LatexDoc:
        preset = self.document.preset
        doc = LatexDoc(documentclass="article")

        # # 📦 Packages
        # if preset and preset.packages:
        #     for pkg in preset.packages:
        #         doc.packages.append(Package(pkg))

        # # 📜 Preamble
        # if preset and preset.preamble:
        #     doc.preamble.append(NoEscape(preset.preamble.strip()))

        # 🖋️ Metadata
        doc.preamble.append(Command('title', self.document.title))
        author = self.document.department.owner.get_full_name() if self.document.department else "Unknown"
        doc.preamble.append(Command('author', author))
        doc.preamble.append(Command('date', NoEscape(r'\today')))
        doc.append(NoEscape(r'\maketitle'))

        # 🧠 Abstract
        if hasattr(self.document, "summary") and self.document.summary:
            doc.append(NoEscape(r'\begin{abstract}'))
            doc.append(NoEscape(self.document.summary.strip()))
            doc.append(NoEscape(r'\end{abstract}'))

        # 🧩 Body (Pandoc output)
        doc.append(NoEscape(body.strip()))

        # 📝 Footer
        if preset and preset.footer_html:
            doc.append(NoEscape(r'\vfill'))
            doc.append(NoEscape(r'\begin{center}'))
            doc.append(NoEscape(r'\textit{' + preset.footer_html.strip() + '}'))
            doc.append(NoEscape(r'\end{center}'))

        return doc





