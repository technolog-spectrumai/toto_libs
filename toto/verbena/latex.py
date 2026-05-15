from __future__ import annotations

from html import unescape
from html.parser import HTMLParser

from django.utils.html import strip_tags


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


def html_to_plain_text(value: str) -> str:
    return unescape(strip_tags(value or "")).strip()


class TrixHTMLToLatexParser(HTMLParser):
    """Best-effort converter for HTML saved by django-trix-editor/Trix."""

    BLOCK_TAGS = {"address", "article", "aside", "div", "figure", "footer", "header", "main", "p", "section"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.list_stack: list[str] = []
        self.href_stack: list[str | None] = []

    def text(self) -> str:
        return "".join(self.parts).strip()

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag in self.BLOCK_TAGS:
            self._paragraph_break()
        elif tag in {"br"}:
            self.parts.append("\n")
        elif tag in {"strong", "b"}:
            self.parts.append(r"\textbf{")
        elif tag in {"em", "i"}:
            self.parts.append(r"\emph{")
        elif tag == "a":
            self.href_stack.append(attrs_dict.get("href"))
            self.parts.append(r"\href{")
            self.parts.append(escape_latex(attrs_dict.get("href", "")))
            self.parts.append("}{")
        elif tag == "blockquote":
            self._paragraph_break()
            self.parts.append(r"\begin{quote}" + "\n")
        elif tag == "pre":
            self._paragraph_break()
            self.parts.append(r"\begin{verbatim}" + "\n")
        elif tag == "code":
            self.parts.append(r"\texttt{")
        elif tag == "ul":
            self._paragraph_break()
            self.list_stack.append("itemize")
            self.parts.append(r"\begin{itemize}" + "\n")
        elif tag == "ol":
            self._paragraph_break()
            self.list_stack.append("enumerate")
            self.parts.append(r"\begin{enumerate}" + "\n")
        elif tag == "li":
            self.parts.append(r"\item ")
        elif tag in {"h1", "h2", "h3"}:
            self._paragraph_break()
            command = {
                "h1": "subsection",
                "h2": "subsubsection",
                "h3": "paragraph",
            }[tag]
            self.parts.append(rf"\{command}{{")
        elif tag in {"h4", "h5", "h6"}:
            self._paragraph_break()
            self.parts.append(r"\textbf{")

    def handle_endtag(self, tag):
        if tag in self.BLOCK_TAGS:
            self._paragraph_break()
        elif tag in {"strong", "b", "em", "i", "code"}:
            self.parts.append("}")
        elif tag == "a":
            if self.href_stack:
                self.href_stack.pop()
            self.parts.append("}")
        elif tag == "blockquote":
            self.parts.append("\n" + r"\end{quote}")
            self._paragraph_break()
        elif tag == "pre":
            self.parts.append("\n" + r"\end{verbatim}")
            self._paragraph_break()
        elif tag in {"ul", "ol"}:
            environment = self.list_stack.pop() if self.list_stack else ("itemize" if tag == "ul" else "enumerate")
            self.parts.append(rf"\end{{{environment}}}" + "\n")
            self._paragraph_break()
        elif tag == "li":
            self.parts.append("\n")
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("}")
            self._paragraph_break()

    def handle_data(self, data):
        if self.parts and not self.parts[-1].endswith((" ", "\n", "{")) and data and not data.startswith((" ", "\n")):
            self.parts.append(" ")
        self.parts.append(escape_latex(data))

    def handle_entityref(self, name):
        self.parts.append(escape_latex(unescape(f"&{name};")))

    def handle_charref(self, name):
        self.parts.append(escape_latex(unescape(f"&#{name};")))

    def _paragraph_break(self):
        text = "".join(self.parts)
        if text and not text.endswith("\n\n"):
            self.parts.append("\n\n")


def trix_html_to_latex(value: str) -> str:
    if not value:
        return ""
    parser = TrixHTMLToLatexParser()
    try:
        parser.feed(value)
        parser.close()
        converted = parser.text()
    except Exception:
        converted = ""
    if converted:
        return converted
    return escape_latex(html_to_plain_text(value))


def page_to_article_latex(page) -> str:
    title = escape_latex(page.title)
    description = escape_latex(page.description)

    lines = [
        r"\documentclass{article}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage[T1]{fontenc}",
        r"\usepackage{hyperref}",
        r"\usepackage{enumitem}",
        "",
        rf"\title{{{title}}}",
        r"\date{\today}",
        "",
        r"\begin{document}",
        r"\maketitle",
        "",
    ]

    if description:
        lines.extend([description, ""])

    for section in page.sections.all().order_by("order", "id"):
        lines.extend([
            rf"\section{{{escape_latex(section.title or 'Section')}}}",
            trix_html_to_latex(section.content) or r"\vspace{1em}",
            "",
        ])

    lines.extend([r"\end{document}", ""])
    return "\n".join(lines)
