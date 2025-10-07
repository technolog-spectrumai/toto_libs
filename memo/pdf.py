from django.conf import settings
from pathlib import Path
import subprocess

def escape(text):
    replacements = {
        '\\': r'\textbackslash{}',
        '&': r'\&',
        '%': r'\%',
        '$': r'\$',
        '#': r'\#',
        '_': r'\_',
        '{': r'\{',
        '}': r'\}',
        '~': r'\textasciitilde{}',
        '^': r'\textasciicircum{}',
    }
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)
    return text

class MemoDeckCompiler:
    def __init__(self, deck):
        self.deck = deck

    def generate_pdf(self):
        tex_path = Path(settings.MEDIA_ROOT) / f"{self.deck.title.replace(' ', '_')}.tex"
        pdf_path = tex_path.with_suffix('.pdf')

        with open(tex_path, 'w', encoding='utf-8') as f:
            # Header and preamble
            f.write("\\documentclass{beamer}\n")
            f.write("\\usetheme{Rochester}\n")
            f.write("\\usecolortheme{seahorse}\n")
            f.write("\\usepackage[T1]{fontenc}\n")
            f.write("\\usepackage[utf8]{inputenc}\n")
            f.write("\\usepackage{graphicx}\n\n")

            # Metadata
            f.write(f"\\title{{{escape(self.deck.title)}}}\n")
            f.write(f"\\author{{{escape(self.deck.author.username)}}}\n")
            f.write("\\date{\\today}\n\n")

            # Begin document
            f.write("\\begin{document}\n\n")

            # Title page
            f.write("  \\begin{frame}\n")
            f.write("    \\titlepage\n")
            f.write("  \\end{frame}\n\n")

            # TOC frame
            f.write("  \\begin{frame}\n")
            f.write("    \\frametitle{Contents}\n")
            f.write("    \\tableofcontents\n")
            f.write("  \\end{frame}\n\n")

            # Card frames
            for card in self.deck.cards.all():
                f.write(f"  \\section{{{escape(card.title)}}}\n")
                f.write("  \\begin{frame}\n")
                f.write(f"    \\frametitle{{{escape(card.title)}}}\n")
                content = escape(card.content)
                f.write(f"    {content}\n")

                if card.image:
                    image_path = escape(str(Path(settings.MEDIA_ROOT) / card.image.name).replace('\\', '/'))
                    f.write(f"    \\includegraphics[width=0.5\\linewidth]{{{image_path}}}\n")
                f.write("  \\end{frame}\n\n")

            # End document
            f.write("\\end{document}\n")

        # Compile manually
        try:
            subprocess.run(['pdflatex', '-interaction=nonstopmode', str(tex_path)], check=True)
        except subprocess.CalledProcessError as e:
            print("LaTeX compilation failed:", e)
            return None

        return pdf_path if pdf_path.exists() else None
