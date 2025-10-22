from pathlib import Path
from django.core.files.base import ContentFile
from latextile.models import LatexProject, TexFile
from vault.models import VaultFile
from django.conf import settings

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


class DeckLatexExporter:
    def __init__(self, deck):
        self.deck = deck
        self.preset = getattr(deck, 'latex_preset', None)

    def generate_tex_content(self):
        lines = []

        lines.append("\\documentclass{beamer}")
        if self.preset:
            lines.append(f"\\usetheme{{{self.preset.theme}}}")
            lines.append(f"\\usecolortheme{{{self.preset.color_theme}}}")
        else:
            lines.append("\\usetheme{Rochester}")
            lines.append("\\usecolortheme{seahorse}")

        if self.preset and self.preset.packages:
            for pkg in self.preset.packages:
                lines.append(f"\\usepackage{{{pkg}}}")
        else:
            lines.append("\\usepackage[T1]{fontenc}")
            lines.append("\\usepackage[utf8]{inputenc}")
            lines.append("\\usepackage{graphicx}")

        lines.append(f"\n\\title{{{escape(self.deck.title)}}}")
        lines.append(f"\\author{{{escape(self.deck.author.username)}}}")
        lines.append("\\date{\\today}\n")
        lines.append("\\begin{document}\n")

        if not self.preset or self.preset.include_title_page:
            lines.append("  \\begin{frame}")
            lines.append("    \\titlepage")
            lines.append("    SpectrumaAi.pl")
            lines.append("  \\end{frame}\n")

        if not self.preset or self.preset.include_table_of_contents:
            lines.append("  \\begin{frame}")
            lines.append("    \\frametitle{Contents}")
            lines.append("    \\tableofcontents")
            lines.append("  \\end{frame}\n")

        for card in self.deck.cards.all():
            lines.append(f"  \\section{{{escape(card.title)}}}")
            lines.append("  \\begin{frame}")
            lines.append(f"    \\frametitle{{{escape(card.title)}}}")
            lines.append(f"    {escape(card.content)}")
            if card.image:
                image_path = str(Path(settings.MEDIA_ROOT) / card.image.name).replace('\\', '/')
                lines.append(f"    \\includegraphics[width=0.5\\linewidth]{{{escape(image_path)}}}")
            lines.append("  \\end{frame}\n")

        lines.append("\\end{document}")
        return "\n".join(lines)

    def export_to_latex(self):
        project = LatexProject.objects.create(
            user=self.deck.author,
            name=self.deck.title
        )

        tex_content = self.generate_tex_content()
        tex_filename = f"{self.deck.title.replace(' ', '_')}.tex"

        tex_file = TexFile.objects.create(
            project=project,
            filename=tex_filename
        )
        tex_file.file.save(tex_filename, ContentFile(tex_content), save=True)

        return tex_file
