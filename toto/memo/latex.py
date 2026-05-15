from __future__ import annotations

import re


LATEX_SPECIAL_CHARS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def escape_latex(value: str) -> str:
    return "".join(LATEX_SPECIAL_CHARS.get(char, char) for char in value or "")


def clean_theme_name(value: str) -> str:
    theme = re.sub(r"[^A-Za-z0-9_-]", "", value or "")
    return theme or "Madrid"


def deck_to_beamer_latex(deck, theme: str = "Madrid") -> str:
    theme = clean_theme_name(theme)
    title = escape_latex(deck.title)
    author = escape_latex(deck.author.get_full_name() or deck.author.username)
    description = escape_latex(deck.description)

    lines = [
        r"\documentclass{beamer}",
        rf"\usetheme{{{theme}}}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage[T1]{fontenc}",
        "",
        rf"\title{{{title}}}",
        rf"\author{{{author}}}",
        r"\date{\today}",
        "",
        r"\begin{document}",
        "",
        r"\begin{frame}",
        r"\titlepage",
        r"\end{frame}",
        "",
    ]

    if description:
        lines.extend([
            r"\begin{frame}{Overview}",
            description,
            r"\end{frame}",
            "",
        ])

    for card in deck.cards.all().order_by("order", "id"):
        lines.extend([
            rf"\begin{{frame}}{{{escape_latex(card.title)}}}",
            escape_latex(card.content).replace("\n", "\n\n"),
        ])
        if card.diagram:
            lines.append("")
            lines.append(r"\vspace{0.5em}")
            lines.append(escape_latex(f"Diagram: {card.diagram}"))
        if card.image:
            lines.append("")
            lines.append(r"\vspace{0.5em}")
            lines.append(escape_latex(f"Image: {card.image.name}"))
        lines.extend([
            r"\end{frame}",
            "",
        ])

    lines.append(r"\end{document}")
    lines.append("")
    return "\n".join(lines)
