"""LaTeX helpers for admin-editable ContractTemplate sources.

The template is rendered with HTML autoescaping OFF (see notarius.latex.render_latex),
so HTML entities never appear — but LaTeX has its own special characters. Pipe any
contract text through ``|latexescape`` to make it safe inside LaTeX.
"""
from django import template

register = template.Library()

# Order matters: backslash first so we don't double-escape the replacements.
_REPLACEMENTS = [
    ("\\", r"\textbackslash{}"),
    ("{", r"\{"),
    ("}", r"\}"),
    ("$", r"\$"),
    ("&", r"\&"),
    ("#", r"\#"),
    ("%", r"\%"),
    ("_", r"\_"),
    ("^", r"\textasciicircum{}"),
    ("~", r"\textasciitilde{}"),
]


@register.filter(name="latexescape")
def latexescape(value) -> str:
    if value is None:
        return ""
    text = str(value)
    for needle, repl in _REPLACEMENTS:
        text = text.replace(needle, repl)
    return text
