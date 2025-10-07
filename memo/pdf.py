from django.conf import settings
from pathlib import Path
import os
import subprocess

class MemoDeckCompiler:
    def __init__(self, deck):
        self.deck = deck

    def generate_pdf(self):
        tex_path = Path(settings.MEDIA_ROOT) / f"{self.deck.title.replace(' ', '_')}.tex"
        print("---->", tex_path)
        pdf_path = tex_path.with_suffix('.pdf')

        with open(tex_path, 'w', encoding='utf-8') as f:
            # Header and preamble
            f.write("\\documentclass{beamer}\n")
            f.write("\\usetheme{Rochester}\n")
            f.write("\\usepackage[T1]{fontenc}\n")
            f.write("\\usepackage[utf8]{inputenc}\n")
            f.write("\\usepackage{graphicx}\n\n")

            # Metadata
            f.write(f"\\title{{{self.deck.title}}}\n")
            f.write(f"\\author{{{self.deck.author.username}}}\n")
            f.write("\\date{\\today}\n\n")

            # Begin document
            f.write("\\begin{document}\n\n")

            # TOC frame
            f.write("  \\begin{frame}\n")
            f.write("    \\frametitle{Contents}\n")
            f.write("    \\tableofcontents\n")
            f.write("  \\end{frame}\n\n")

            # Card frames
            for card in self.deck.cards.all():
                f.write(f"  \\section{{{card.title}}}\n")
                f.write("  \\begin{frame}\n")
                f.write(f"    \\frametitle{{{card.title}}}\n")
                content = card.content.replace('%', r'\%')
                f.write(f"    {content}\n")
                f.write("  \\end{frame}\n\n")

                if card.image:
                    image_path = str(Path(settings.MEDIA_ROOT) / card.image.name).replace('\\', '/')
                    f.write("  \\begin{frame}\n")
                    f.write("    \\frametitle{Diagram}\n")
                    f.write(f"    \\includegraphics[width=\\linewidth]{{{image_path}}}\n")
                    f.write("  \\end{frame}\n\n")

            # End document
            f.write("\\end{document}\n")

        # Compile manually
        try:
            subprocess.run(['latexmk', '--pdf', '--interaction=nonstopmode', str(tex_path)], check=True)
        except subprocess.CalledProcessError as e:
            print("LaTeX compilation failed:", e)
            return None

        return pdf_path if pdf_path.exists() else None
