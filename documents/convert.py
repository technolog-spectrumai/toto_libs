import subprocess
from .models import LatexDocument, HTMLDocument

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
        converter = PandocConverter(from_format='html', to_format='latex')
        return converter.convert(self.document.content)




