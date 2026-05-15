from __future__ import annotations

from toto.verbena.latex import escape_latex, trix_html_to_latex


def deck_to_article_latex(deck) -> str:
    title = escape_latex(deck.title)
    author = escape_latex(deck.author.get_full_name() or deck.author.username)
    description = escape_latex(deck.description)

    lines = [
        r"\documentclass{article}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage[T1]{fontenc}",
        r"\usepackage{hyperref}",
        r"\usepackage{enumitem}",
        "",
        rf"\title{{{title}}}",
        rf"\author{{{author}}}",
        r"\date{\today}",
        "",
        r"\begin{document}",
        r"\maketitle",
        "",
    ]

    if description:
        lines.extend([description, ""])

    for card in deck.cards.all().order_by("order", "id"):
        lines.extend([
            rf"\section*{{{escape_latex(card.title)}}}",
            trix_html_to_latex(card.content) or r"\vspace{1em}",
        ])
        if card.diagram:
            lines.extend(["", escape_latex(f"Diagram: {card.diagram}")])
        if card.image:
            lines.extend(["", escape_latex(f"Image: {card.image.name}")])
        lines.append("")

    lines.extend([r"\end{document}", ""])
    return "\n".join(lines)
