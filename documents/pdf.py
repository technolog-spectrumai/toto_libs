import os
from .models import LatexDocument
import tempfile
from latex import build_pdf


class LatexCompiler:
    def __init__(self, document: LatexDocument):
        self.document = document

        if not isinstance(document, LatexDocument):
            raise TypeError(f"Expected LatexDocument, got {type(document)}")

    def generate_pdf(self):

        ext = ".pdf"
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as temp_file:
            output_path = temp_file.name

        pdf = build_pdf(self.document.content)
        pdf.save_to(output_path)

        # Confirm the file exists and return its path
        if os.path.exists(output_path):
            return output_path
        else:
            raise FileNotFoundError(f"PDF generation failed at {output_path}")


