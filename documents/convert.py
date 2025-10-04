from bs4 import BeautifulSoup
from plasTeX.TeX import TeX

# ────────────────────────────────────────────────
# 🔁 Symmetrical Conversion Maps
# ────────────────────────────────────────────────

HTML_TO_LATEX = {
    "h1": r"\section{%s}",
    "h2": r"\subsection{%s}",
    "h3": r"\subsubsection{%s}",
    "p": r"%s\\",
    "strong": r"\textbf{%s}",
    "em": r"\emph{%s}",
    "ul": r"\begin{itemize}%s\end{itemize}",
    "ol": r"\begin{enumerate}%s\end{enumerate}",
    "li": r"\item %s"
}

LATEX_TO_HTML = {
    "section": "h1",
    "subsection": "h2",
    "subsubsection": "h3",
    "textbf": "strong",
    "emph": "em",
    "par": "p",
    "itemize": "ul",
    "enumerate": "ol",
    "item": "li"
}

# ────────────────────────────────────────────────
# 🔁 HTML → LaTeX Converter
# ────────────────────────────────────────────────

class HTMLToLatexConverter:
    def __init__(self, text, conversion_map=None):
        self.text = text
        self.conversion_map = conversion_map or HTML_TO_LATEX

    def convert(self):
        soup = BeautifulSoup(self.text, 'html.parser')
        return self._convert_node(soup.body or soup)

    def _convert_node(self, node):
        latex_parts = []

        for child in node.children:
            if isinstance(child, str):
                latex_parts.append(child)  # Preserve raw spacing
                continue

            tag = child.name
            inner = self._convert_node(child)

            if tag in ["ul", "ol"]:
                items = ''.join([self._convert_node(li) for li in child.find_all("li", recursive=False)])
                latex_parts.append(self.conversion_map[tag] % items)
            elif tag in self.conversion_map:
                latex_parts.append(self.conversion_map[tag] % inner)
            else:
                latex_parts.append(inner)

        return ''.join(latex_parts)



# ────────────────────────────────────────────────
# 🔁 LaTeX → HTML Converter
# ────────────────────────────────────────────────

class LatexToHTMLConverter:
    def __init__(self, text, conversion_map=None):
        self.text = text
        self.conversion_map = conversion_map or LATEX_TO_HTML

    def convert(self):
        tex = TeX()
        tex.input(self.text)
        dom = tex.parse()
        return self._convert_node(dom)

    def _convert_node(self, node):
        html_parts = []

        for child in node.childNodes:
            if child.nodeType == child.TEXT_NODE:
                html_parts.append(child.data)  # preserve raw spacing
                continue

            tag_name = child.nodeName
            inner_html = self._convert_node(child)

            if tag_name in self.conversion_map:
                html_tag = self.conversion_map[tag_name]
                html_parts.append(f"<{html_tag}>{inner_html}</{html_tag}>")
            else:
                html_parts.append(inner_html)

        return ''.join(html_parts)
