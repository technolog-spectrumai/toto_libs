import os.path
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
        self.preset = getattr(deck, 'latex_preset', None)
        self.output_dir = Path(settings.MEDIA_ROOT) / f"{self.deck.title.replace(' ', '_')}_files"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.tex_path = self.output_dir / f"{self.deck.title.replace(' ', '_')}.tex"
        self.pdf_path = self.tex_path.with_suffix('.pdf')

    def build_tex(self):
        with open(self.tex_path, 'w', encoding='utf-8') as f:
            # Document class
            f.write("\\documentclass{beamer}\n")

            # Theme and color
            if self.preset:
                f.write(f"\\usetheme{{{self.preset.theme}}}\n")
                f.write(f"\\usecolortheme{{{self.preset.color_theme}}}\n")
            else:
                f.write("\\usetheme{Rochester}\n")
                f.write("\\usecolortheme{seahorse}\n")

            # Packages
            if self.preset and self.preset.packages:
                for pkg in self.preset.packages:
                    f.write(f"\\usepackage{{{pkg}}}\n")
            else:
                f.write("\\usepackage[T1]{fontenc}\n")
                f.write("\\usepackage[utf8]{inputenc}\n")
                f.write("\\usepackage{graphicx}\n")

            # Metadata
            f.write(f"\n\\title{{{escape(self.deck.title)}}}\n")
            f.write(f"\\author{{{escape(self.deck.author.username)}}}\n")
            f.write("\\date{\\today}\n\n")

            # Begin document
            f.write("\\begin{document}\n\n")

            # Title page
            if not self.preset or self.preset.include_title_page:
                f.write("  \\begin{frame}\n")
                f.write("    \\titlepage\n")
                f.write("  \\end{frame}\n\n")

            # TOC frame
            if not self.preset or self.preset.include_table_of_contents:
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

        return self.tex_path

    def compile_pdf(self):
        try:
            subprocess.run([
                'pdflatex',
                '-interaction=nonstopmode',
                f'-output-directory={self.output_dir}',
                str(self.tex_path)
            ], check=True)
        except subprocess.CalledProcessError as e:
            print("LaTeX compilation failed:", e)
            return None

        return self.pdf_path if self.pdf_path.exists() else None

    def generate_pdf(self):
        self.build_tex()
        return self.compile_pdf()
