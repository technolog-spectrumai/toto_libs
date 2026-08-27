"""Turning a standalone HTML page into a Cyprian document.

It lives HERE, in cyprian, and not in the viewer that offers it. `toto.htmlview`
guarantees it "has nowhere to write" — asserted against its urlconf by
`primula/tests/test_readonly.py`, on the grounds that a missing endpoint cannot
be flipped from a console the way a UI flag can. Converting CREATES a file, so
an endpoint that does it in htmlview would have quietly demoted that guarantee
to make one feature convenient. The viewer links here instead, and keeps it.

It is also simply where the knowledge belongs: what an empty document is, and
what part of a page can become one, are questions only cyprian can answer.

An htmlview document is a WHOLE HTML file — doctype, `<head>`, its own
`<style>`, a `<body>`. A Cyprian document is a title plus ONE rich-text body
(`document_format.Document.content` is an HTML fragment). The two are not the
same thing, and pretending otherwise is how somebody loses a page's styling
without being told.

So this converts, and the page that offers it says plainly what is lost:

* the `<body>` content carries over, tags and all;
* `<head>`, `<title>`, `<style>` and every inline `style=` rule DO NOT — Cyprian
  has its own font and margin model and no place to put a stylesheet;
* the ORIGINAL FILE IS NOT TOUCHED. A new document is created beside it. That is
  deliberate: `settle()`/`save_version` capture bytes AFTER a write, so an
  in-place conversion would replace the HTML with document XML and leave no
  version holding the original. It would also change `file_type` to "document",
  which drops the file out of htmlview's own listing — the page the person
  clicked Edit on.
"""

from __future__ import annotations

import re

#: Everything between the body tags. A page with no `<body>` is treated as a
#: fragment and used whole, which is what a hand-written snippet usually is.
_BODY = re.compile(r"<body\b[^>]*>(.*?)</body\s*>", re.I | re.S)
_TITLE = re.compile(r"<title\b[^>]*>(.*?)</title\s*>", re.I | re.S)
#: `<style>` and `<script>` inside the body. The sanitiser drops scripts anyway;
#: a stylesheet is dropped here because it would otherwise be rendered as text.
_DROP = re.compile(r"<(style|script)\b[^>]*>.*?</\1\s*>", re.I | re.S)


def body_of(html: str) -> str:
    """The editable part of a page."""
    match = _BODY.search(html or "")
    body = match.group(1) if match else (html or "")
    return _DROP.sub("", body).strip()


def title_of(html: str, *, fallback: str = "") -> str:
    """What the page calls itself, else the filename without its extension."""
    match = _TITLE.search(html or "")
    if match:
        title = re.sub(r"\s+", " ", match.group(1)).strip()
        if title:
            return title
    return fallback.rsplit(".", 1)[0] if "." in fallback else fallback


def document_title(vault_file, html: str) -> str:
    return title_of(html, fallback=vault_file.title)


def convert(vault_file, html: str, *, user):
    """Create the Cyprian document for this page, or return the existing one.

    Idempotent through `bridge.open_document`, which adopts a same-named
    document already sitting in the target folder rather than making a second
    one — so clicking Convert twice returns the writer to the same file instead
    of forking the person's work.
    """
    from . import bridge

    stem = vault_file.title.rsplit(".", 1)[0] or "document"
    return bridge.open_document(
        key="htmlview.source",
        ref=str(vault_file.pk),
        title=f"{stem}.xml",
        seed_html=body_of(html),
        owner=user,
        bucket=vault_file.bucket,
        directory=vault_file.directory,
        toc=False,
        document_title=document_title(vault_file, html),
    )


#: What the writer would LOSE. TipTap keeps its own schema: scripts, styles,
#: stylesheets, classes, ids, inline styles and embedded frames all vanish on
#: the first save. A page carrying any of them is not "hard" — it is simply
#: not round-trippable, and pretending otherwise is how somebody's stylesheet
#: disappears without being told. Those pages get the source editor.
_INCOMPATIBLE = re.compile(
    r"<\s*(script|style|link|iframe|object|embed|form|frameset)\b"
    r"|\sstyle\s*="
    r"|\son[a-z]+\s*="
    r"|\sclass\s*="
    r"|\sid\s*=",
    re.I)


def is_compatible(html: str) -> bool:
    """Would editing this page in the writer lose anything? False = yes."""
    return not _INCOMPATIBLE.search(html or "")


def page_of(document) -> str:
    """The inverse of `body_of`: a whole page around the writer's body.

    Only for pages `is_compatible` admitted — their head was trivial by
    definition, so a regenerated one loses nothing. `document.content` is
    already sanitised (`Document.from_dict` runs `sanitize_content` on the
    way in), which is what makes writing it back a fact rather than a hope.
    """
    title = (document.title or "").strip()
    return ("<!doctype html>\n<html>\n<head>\n<meta charset=\"utf-8\">\n"
            f"<title>{title}</title>\n</head>\n<body>\n"
            f"{document.content or ''}\n</body>\n</html>\n")
