"""Scanner parameters: defaults here, overrides in one DB row.

The overrides live in ``models.ScannerConfig.params`` — a single JSON field,
deliberately, so adding a parameter is a new key and a default below, never a
migration. Scanners read them AT SCAN TIME through :func:`params`: the scanner
modules are imported from ``ready()`` where the database may not exist yet, so
nothing here touches the ORM at import.

What is configurable is bounded on purpose. The core refusals — script in
markup, ``/JavaScript`` in a PDF — are NOT parameters: a knob that can switch
off the reason the scanner exists is not configuration, it is a bypass wearing
a settings page.
"""

from __future__ import annotations

#: Every known parameter and its default. The Settings form renders from this,
#: so a new entry appears there automatically.
DEFAULTS = {
    # How deep nesting may go before a JSON document is refused (the
    # billion-laughs family arrives as depth, not size).
    "json_max_depth": 40,
    # The largest file an on-demand or door scan will read, in megabytes.
    # Bigger is refused as wrong-shape rather than silently skipped: "too big
    # to check" must not look like "checked".
    "scan_max_mb": 10,
    # The PDF markers held conservatively. The hard core (/JavaScript, /JS,
    # /OpenAction, /Launch) is not configurable.
    "pdf_refuse_aa": True,            # /AA — additional actions
    "pdf_refuse_xfa": True,           # /XFA — XFA forms
    "pdf_refuse_embedded": True,      # /EmbeddedFile
    "pdf_refuse_richmedia": True,     # /RichMedia
    "pdf_refuse_encrypted": True,     # /Encrypt — cannot be inspected
}


def params() -> dict:
    """Defaults merged with the stored overrides. Never raises.

    A broken row or an unmigrated database yields the defaults — the scanner
    keeps scanning at its strictest known configuration rather than crashing a
    save."""
    merged = dict(DEFAULTS)
    try:
        from toto.antivirus.models import ScannerConfig

        stored = ScannerConfig.get().params or {}
        for key in DEFAULTS:
            if key in stored:
                merged[key] = stored[key]
    except Exception:  # noqa: BLE001 - defaults are the safe direction
        pass
    return merged
