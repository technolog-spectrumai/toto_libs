"""Re-export of :mod:`toto.core.qr` — the module moved to toto-base (8/2026).

The polls ledger checkpoint needed the same QR primitive, and toto-base may
not import toto-auth: the dependency edge points the other way. The code now
lives where both packages can reach it; this shim keeps every existing
sso_core import path working — the access.py precedent, nothing outside had
to move at once.
"""
from toto.core.qr import (  # noqa: F401
    DEFAULT_SCALE,
    QRError,
    QUIET_ZONE_MODULES,
    _decode_resilient,
    _upscale,
    _with_quiet_zone,
    read,
    render_data_uri,
)
