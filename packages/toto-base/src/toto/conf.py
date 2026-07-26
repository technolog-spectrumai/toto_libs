"""Host-configurable filesystem locations.

toto historically resolved a few directories relative to the monorepo layout
(the host's BASE_DIR or this package's own location on disk).  Hosts should
now set TOTO_DATA_DIR / TOTO_RUN_DIR explicitly; the legacy expressions are
kept as fallbacks so behavior is unchanged when the settings are absent.
"""
from pathlib import Path

from django.conf import settings


def data_dir(legacy_default) -> Path:
    """Directory holding shared seed/branding data (fonts.json, themes/, img/).

    Returns the TOTO_DATA_DIR setting when defined, else ``legacy_default``
    (each call site passes its pre-split path expression unchanged).
    """
    configured = getattr(settings, "TOTO_DATA_DIR", None)
    return Path(configured) if configured else Path(legacy_default)


def run_dir() -> Path:
    """Directory for runtime artifacts shared with the host (vault passwords).

    Returns the TOTO_RUN_DIR setting when defined, else the legacy
    ``BASE_DIR.parent / "run"``.
    """
    configured = getattr(settings, "TOTO_RUN_DIR", None)
    return Path(configured) if configured else Path(settings.BASE_DIR).parent / "run"
