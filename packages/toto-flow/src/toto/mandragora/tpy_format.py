"""
.tpy notebook serialization — XML format.

A ``.tpy`` file is a single self-contained XML document that stores everything a
notebook needs: the dependency list, and for every cell its source code, the
captured console output (stdout / stderr / execution count) and any rich
output (images, repr data) produced on the last run.

The file is the single source of truth: it is parsed into the in-memory
:class:`TpyNotebook` structure when the editor opens, and serialized back when
the user presses Save.  Nothing about a ``.tpy`` notebook lives in the database.

Document shape::

    <?xml version="1.0" encoding="utf-8"?>
    <notebook version="1" title="My Notebook">
      <dependencies>
        <dependency package="numpy" version="&gt;=1.21"/>
        <dependency package="pandas" version=""/>
      </dependencies>
      <cells>
        <cell type="code" execution_count="1">
          <source>import numpy as np
    print(np.arange(3))</source>
          <stdout>[0 1 2]
    </stdout>
          <stderr></stderr>
          <rich_output>[]</rich_output>
        </cell>
        <cell type="markdown">
          <source># Title</source>
        </cell>
      </cells>
    </notebook>

Free-text fields (``source``, ``stdout``, ``stderr``) are stored verbatim as
element text.  XML 1.0 forbids most control characters (e.g. the ESC byte used
by ANSI-coloured tracebacks); when such characters are present the text is
stored base64-encoded with an ``enc="base64"`` attribute so the document stays
valid and round-trips exactly.  ``rich_output`` is a JSON blob (always ASCII, so
always XML-safe).
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

FORMAT_VERSION = "1"

CODE = "code"
MARKDOWN = "markdown"
_CELL_TYPES = (CODE, MARKDOWN)

# Characters that are illegal in XML 1.0 text content.  Anything matching this
# forces base64 encoding of the field so the document stays well-formed.
# Valid: #x9 | #xA | #xD | [#x20-#xD7FF] | [#xE000-#xFFFD] | [#x10000-#x10FFFF]
_INVALID_XML_CHARS = re.compile(
    "[^\x09\x0a\x0d\x20-퟿-�\U00010000-\U0010ffff]"
)


class TpyParseError(ValueError):
    """Raised when a .tpy document cannot be parsed."""


@dataclass
class TpyCell:
    cell_type: str = CODE
    source: str = ""
    execution_count: int = 0
    stdout: str = ""
    stderr: str = ""
    rich_output: list = field(default_factory=list)

    def is_code(self) -> bool:
        return self.cell_type == CODE

    def to_dict(self) -> dict:
        return {
            "cell_type": self.cell_type,
            "source": self.source,
            "execution_count": self.execution_count,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "rich_output": self.rich_output or [],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TpyCell":
        cell_type = data.get("cell_type") or data.get("type") or CODE
        if cell_type not in _CELL_TYPES:
            cell_type = CODE
        try:
            exec_count = int(data.get("execution_count") or 0)
        except (TypeError, ValueError):
            exec_count = 0
        rich = data.get("rich_output") or []
        if not isinstance(rich, list):
            rich = []
        return cls(
            cell_type=cell_type,
            source=data.get("source") or "",
            execution_count=exec_count,
            stdout=data.get("stdout") or "",
            stderr=data.get("stderr") or "",
            rich_output=rich,
        )


@dataclass
class TpyDependency:
    package_name: str
    version_spec: str = ""

    def pip_specifier(self) -> str:
        return f"{self.package_name}{self.version_spec}" if self.version_spec else self.package_name

    def to_dict(self) -> dict:
        return {"package_name": self.package_name, "version_spec": self.version_spec}

    @classmethod
    def from_dict(cls, data: dict) -> "TpyDependency | None":
        name = (data.get("package_name") or "").strip()
        if not name:
            return None
        return cls(package_name=name, version_spec=(data.get("version_spec") or "").strip())


@dataclass
class TpyNotebook:
    title: str = ""
    dependencies: list[TpyDependency] = field(default_factory=list)
    cells: list[TpyCell] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "dependencies": [d.to_dict() for d in self.dependencies],
            "cells": [c.to_dict() for c in self.cells],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TpyNotebook":
        deps = []
        for raw in data.get("dependencies") or []:
            dep = TpyDependency.from_dict(raw)
            if dep is not None:
                deps.append(dep)
        cells = [TpyCell.from_dict(raw) for raw in data.get("cells") or []]
        return cls(title=data.get("title") or "", dependencies=deps, cells=cells)


def new_notebook(title: str = "") -> TpyNotebook:
    """A blank notebook with a single empty code cell — used for new files."""
    return TpyNotebook(title=title, dependencies=[], cells=[TpyCell(cell_type=CODE)])


# ---------------------------------------------------------------------------
# XML-safe text helpers
# ---------------------------------------------------------------------------

def _set_text(el: ET.Element, text: str) -> None:
    text = text or ""
    if _INVALID_XML_CHARS.search(text):
        el.set("enc", "base64")
        el.text = base64.b64encode(text.encode("utf-8")).decode("ascii")
    else:
        el.text = text


def _get_text(el: "ET.Element | None") -> str:
    if el is None:
        return ""
    raw = el.text or ""
    if el.get("enc") == "base64":
        try:
            return base64.b64decode(raw.encode("ascii")).decode("utf-8")
        except Exception:
            return ""
    return raw


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def dumps(notebook: TpyNotebook) -> str:
    root = ET.Element("notebook", {"version": FORMAT_VERSION, "title": notebook.title or ""})

    deps_el = ET.SubElement(root, "dependencies")
    for dep in notebook.dependencies:
        if not dep.package_name:
            continue
        ET.SubElement(
            deps_el,
            "dependency",
            {"package": dep.package_name, "version": dep.version_spec or ""},
        )

    cells_el = ET.SubElement(root, "cells")
    for cell in notebook.cells:
        attrs = {"type": cell.cell_type if cell.cell_type in _CELL_TYPES else CODE}
        if cell.is_code():
            attrs["execution_count"] = str(cell.execution_count or 0)
        cell_el = ET.SubElement(cells_el, "cell", attrs)

        _set_text(ET.SubElement(cell_el, "source"), cell.source)

        if cell.is_code():
            _set_text(ET.SubElement(cell_el, "stdout"), cell.stdout)
            _set_text(ET.SubElement(cell_el, "stderr"), cell.stderr)
            rich_el = ET.SubElement(cell_el, "rich_output")
            # json.dumps with ensure_ascii (default) escapes control chars, so
            # the result is always XML-safe ASCII.
            rich_el.text = json.dumps(cell.rich_output or [])

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + body + "\n"


def is_notebook(xml: "str | bytes") -> bool:
    """True if ``xml`` is a notebook document (root ``<notebook>``).

    Cheap identity check for "is this ordinary XML vault file a notebook?" — used to
    filter the notebook index and gate the editor now that notebooks are stored as
    plain ``.xml`` (``file_type="xml"``) rather than a dedicated ``.tpy`` type.
    """
    try:
        if isinstance(xml, bytes):
            xml = xml.decode("utf-8")
        return ET.fromstring(xml).tag == "notebook"
    except Exception:
        return False


def loads(text: str) -> TpyNotebook:
    text = (text or "").strip()
    if not text:
        return new_notebook()

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise TpyParseError(f"Invalid .tpy document: {exc}") from exc

    if root.tag != "notebook":
        raise TpyParseError(f"Expected root <notebook>, found <{root.tag}>")

    notebook = TpyNotebook(title=root.get("title") or "")

    deps_el = root.find("dependencies")
    if deps_el is not None:
        for dep_el in deps_el.findall("dependency"):
            name = (dep_el.get("package") or "").strip()
            if not name:
                continue
            notebook.dependencies.append(
                TpyDependency(package_name=name, version_spec=(dep_el.get("version") or "").strip())
            )

    cells_el = root.find("cells")
    if cells_el is not None:
        for cell_el in cells_el.findall("cell"):
            cell_type = cell_el.get("type") or CODE
            if cell_type not in _CELL_TYPES:
                cell_type = CODE
            try:
                exec_count = int(cell_el.get("execution_count") or 0)
            except (TypeError, ValueError):
                exec_count = 0

            rich_raw = _get_text(cell_el.find("rich_output"))
            try:
                rich = json.loads(rich_raw) if rich_raw.strip() else []
            except (ValueError, TypeError):
                rich = []
            if not isinstance(rich, list):
                rich = []

            notebook.cells.append(
                TpyCell(
                    cell_type=cell_type,
                    source=_get_text(cell_el.find("source")),
                    execution_count=exec_count,
                    stdout=_get_text(cell_el.find("stdout")),
                    stderr=_get_text(cell_el.find("stderr")),
                    rich_output=rich,
                )
            )

    return notebook
