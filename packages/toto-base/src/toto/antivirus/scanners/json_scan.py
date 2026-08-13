"""Screening JSON.

JSON carries no script and no external reference — there is nothing in the
format to execute. What it does carry is **shape**, and shape is an attack:
a few kilobytes of nested brackets is a stack overflow in any recursive-descent
parser, and Python's is recursive.

So this scanner asks two questions only, and deliberately does not attempt to
judge the data's meaning:

* does it parse at all?
* is it nested deeper than anything legitimate?

Depth is measured on the TEXT, before `json.loads` is called. Measuring it after
parsing would mean surviving the parse first, which is the thing being defended
against.
"""

from __future__ import annotations

import json

from toto.vault.scanning import Verdict

from . import register

REASON_MALFORMED = "malformed"
REASON_DEPTH = "too-deep"

#: Python's own limit is ~1000 frames and `json` recurses per level. Real
#: documents are nowhere near this; a hand-written config is under 10.
MAX_DEPTH = 100


def _depth_exceeded(text: str, limit: int) -> int:
    """The 1-indexed line where nesting first passes `limit`, or 0.

    A character scan, not a parse: it must answer before anything recurses.
    Quoted strings are skipped so a bracket inside a value is not counted.
    """
    depth = 0
    line = 1
    in_string = False
    escaped = False

    for char in text:
        if char == "\n":
            line += 1
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > limit:
                return line
        elif char in "]}":
            depth -= 1
    return 0


def scan_json(text: str) -> Verdict:
    if not isinstance(text, str) or not text.strip():
        return Verdict.refused(REASON_MALFORMED, "empty document")

    line = _depth_exceeded(text, MAX_DEPTH)
    if line:
        return Verdict.refused(
            REASON_DEPTH, f"nested deeper than {MAX_DEPTH} levels", line=line)

    try:
        json.loads(text)
    except ValueError as exc:
        return Verdict.refused(REASON_MALFORMED, str(exc)[:120],
                               line=getattr(exc, "lineno", 0) or 0)
    return Verdict.clean()


register("json", scan_json)
