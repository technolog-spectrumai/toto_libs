"""The Primula sheet format — a **Univer workbook snapshot** (``IWorkbookData``) stored
as JSON in a vault file. This module builds a blank workbook, sniffs whether some JSON is
a Univer workbook, and round-trips the text. It never touches the database.
"""
from __future__ import annotations

import json
import uuid

APP_VERSION = "0.25.1"


def new_workbook(name: str = "New sheet") -> dict:
    """A minimal, valid Univer workbook snapshot with one empty sheet."""
    sid = "sheet-1"
    return {
        "id": f"wb-{uuid.uuid4().hex[:8]}",
        "name": name or "New sheet",
        "appVersion": APP_VERSION,
        "locale": "enUS",
        "sheetOrder": [sid],
        "styles": {},
        "sheets": {
            sid: {
                "id": sid,
                "name": "Sheet1",
                "tabColor": "",
                "rowCount": 100,
                "columnCount": 26,
                "cellData": {},
                "rowData": {},
                "columnData": {},
            }
        },
        "resources": [],
    }


def is_sheet(text: str | bytes) -> bool:
    """True if ``text`` is a Univer workbook snapshot (has ``sheets`` + ``sheetOrder``).

    The cheap identity check that lets an ordinary ``sheet``/``json`` vault file be
    recognised as a Primula sheet — the same idea as ``notarius.is_contract`` /
    ``memo.is_presentation``.
    """
    try:
        if isinstance(text, bytes):
            text = text.decode("utf-8")
        data = json.loads(text)
    except (ValueError, TypeError, UnicodeDecodeError):
        return False
    return (isinstance(data, dict)
            and isinstance(data.get("sheets"), dict)
            and "sheetOrder" in data)


def workbook_from_rows(name: str, rows: list[list], sheet_name: str = "Sheet1") -> dict:
    """Build a workbook snapshot from a 2D list of cell values (strings / numbers).

    Handy for seeds and tests — turns ``[["Item", "Cost"], ["Rent", 1200]]`` into a
    valid Univer workbook. Empty cells (``None``/``""``) are skipped. Cell types follow
    Univer's ``CellValueType`` (string=1, number=2, boolean=3).
    """
    cell_data: dict = {}
    for r, row in enumerate(rows):
        col_map: dict = {}
        for c, val in enumerate(row):
            if val is None or val == "":
                continue
            if isinstance(val, bool):
                cell = {"v": val, "t": 3}
            elif isinstance(val, (int, float)):
                cell = {"v": val, "t": 2}
            else:
                cell = {"v": str(val), "t": 1}
            col_map[str(c)] = cell
        if col_map:
            cell_data[str(r)] = col_map

    wb = new_workbook(name)
    sid = wb["sheetOrder"][0]
    wb["sheets"][sid]["name"] = sheet_name
    wb["sheets"][sid]["cellData"] = cell_data
    return wb


def dumps(workbook: dict) -> str:
    return json.dumps(workbook, ensure_ascii=False)


def loads(text: str) -> dict:
    return json.loads(text)
