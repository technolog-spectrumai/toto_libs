"""A wiki page's prose, stored as an ordinary HTML file.

WHAT THIS REPLACED, and why the replacement is smaller. Until 2026-08-29 the
writer stored its work as **CTML** — an XML container carrying a title, a font,
margins, contents-page and cover flags, and one `<content>` element wrapping a
CDATA block of sanitised HTML. The body was always HTML; the container was the
only thing CTML added, and it added a whole file format, a parser, a sniffer, a
vault file type, a migration, a conversion UI and a second answer to "what is a
document" on a platform whose answer is already "an HTML page".

So the container went and the body stayed. What is lost with it is real and
small: font, margins, `toc` and `cover` were CTML columns with nowhere to go in
an HTML file. None of them was ever read by a wiki page, which is what this
writer now exclusively serves — kanban renders `DocumentationPage.body_html`
and nothing else.

THE SANITISER IS UNCHANGED and is still the point. Everything written here goes
through `toto.antivirus.sanitize.sanitize_content` on the way in, which is what
makes rendering `body_html` with `|safe` a fact rather than a hope.
"""

from __future__ import annotations

import dataclasses

from .from_html import body_of, title_of


def _text(value: str) -> str:
    return ((value or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def page(title: str, body: str) -> str:
    """A whole, standalone HTML page around one sanitised body.

    A full document rather than a bare fragment, because the file is an
    ordinary vault file: somebody will download it, open it in a browser, hand
    it to a tool that has never heard of this platform. A fragment would render
    as a wall of unstyled text in every one of those cases.
    """
    return (
        '<!doctype html>\n<html>\n<head>\n<meta charset="utf-8">\n'
        f"<title>{_text((title or '').strip())}</title>\n"
        "<style>\nbody { font-family: Georgia, 'Times New Roman', serif; "
        "margin: 3rem auto; max-width: 46rem; line-height: 1.5; }\n"
        "</style>\n</head>\n<body>\n"
        f"{body or ''}\n</body>\n</html>\n"
    )


def read(html: str, *, fallback_title: str = "") -> tuple[str, str]:
    """(title, body) out of a stored page.

    Tolerant on purpose: a file that is a bare fragment — one hand-edited in
    ACE, or written by something else entirely — yields itself as the body
    rather than nothing. `body_of` already returns the whole string when there
    is no `<body>`.
    """
    return title_of(html, fallback=fallback_title), body_of(html)


@dataclasses.dataclass
class Document:
    """The writer's in-memory document: a title and a sanitised body.

    Two fields, where CTML's `Document` had eight. The rest — font, margins,
    `toc`, `cover`, `meta`, a block list and stable per-block ids — described a
    container this app no longer has. Nothing read them but CTML's own
    serialiser and the weak meta-only bridge, both of which are gone.

    It survives as a class rather than a bare `(title, body)` tuple because it
    is the currency of the DocumentBridge API: `bridge.write_back(document,
    ...)` reads `document.title` and `document.content`, and out-of-tree
    bridges are entitled to keep working.

    `from_dict` IS THE SANITISATION CHOKE POINT, exactly as CTML's was. Every
    save goes through it, so a body that reaches `content` has been through
    `sanitize_content` — which is what makes kanban rendering `body_html` with
    `|safe` a fact rather than a hope. Do not add a second way in.
    """

    title: str = ""
    content: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "Document":
        from toto.antivirus.sanitize import sanitize_content  # noqa: PLC0415

        data = data or {}
        return cls(
            title=str(data.get("title") or "").strip()[:200],
            content=sanitize_content(data.get("content") or ""),
        )

    @classmethod
    def from_html(cls, html: str, *, fallback_title: str = "") -> "Document":
        """Parse a stored page. Tolerant: see `read`."""
        title, body = read(html, fallback_title=fallback_title)
        return cls.from_dict({"title": title, "content": body})

    def to_dict(self) -> dict:
        return {"title": self.title, "content": self.content}

    def dumps(self) -> str:
        return page(self.title, self.content)


# ── the module-level verbs, kept from the format this replaced ──────────────
#
# CTML's module offered `new_document` / `dumps` / `loads`, and every caller —
# the views, the bridge, two test suites — spoke that vocabulary. The words
# still mean the right things, so they survive as the public surface; what
# changed is only what they read and write.
#
# ONE DIFFERENCE IS SEMANTIC and deliberate: CTML's `loads` RAISED on text it
# could not parse, because an XML container either parses or it does not. This
# one never raises — HTML has no failure mode short of not being text, and
# `from_html` degrades a bare fragment to "the whole thing is the body". A
# caller that used to catch DocumentParseError has nothing to catch.

def new_document(title: str = "") -> Document:
    return Document(title=title)


def dumps(document: Document) -> str:
    return document.dumps()


def loads(text: str) -> Document:
    return Document.from_html(text or "")
