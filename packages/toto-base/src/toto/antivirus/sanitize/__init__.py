"""Allowlist sanitisation for rich text.

Here, and not in the editors, because sanitising is a content-safety decision
and this app is where content safety lives. It moved out of memo and cyprian in
8/2026, when the editors themselves left for the zinnia desktop app and the
server kept only what has to be trusted.

**These REWRITE, and the scanners next door never do.** That is a real
distinction, not an inconsistency: a scanner judges a whole file somebody
already wrote and refuses it, because silently rewriting a document its author
is editing is worse than a refusal they can act on. A sanitiser stands in front
of a `contenteditable` and decides what may be STORED at all, from input the
browser composed. Refusing there would mean a save that fails on a stray
attribute nobody typed. Kept in a separate package for exactly that reason —
nothing under `scanners/` rewrites anything.

Unlike the scanners, this is NOT reachable through `toto.vault.scanning`, and
must never become so. That façade degrades to inert answers where the app is
absent, which is right for screening and catastrophic here: unsanitised HTML
stored once is stored forever. Importers depend on this package directly and a
system check refuses to start without it — see toto/cyprian/checks.py.
"""

from .document import ALLOWED_CLASSES, SELF_CLOSING, sanitize_content  # noqa: F401
from .markup import (  # noqa: F401
    plain_text,
    sanitize_inline,
    sanitize_rich,
    sanitize_svg,
)
