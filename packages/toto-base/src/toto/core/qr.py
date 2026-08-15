"""QR rendering and reading, on the libraries the hosts already install.

Lifted verbatim from ``toto.sso_core.qr`` (8/2026), which now re-exports from
here: the polls ledger checkpoint needed the same primitive and toto-base may
not import toto-auth — the dependency points the other way. Everything below,
including the quiet-zone trap and the resilient decode, is the original text.

Federation pairing moves one ~110-character string from one platform's admin to
another's. A QR code is the convenient way to carry it across an air gap — a
phone camera, a screenshot — but it is only ever a *transport*. The same string
sits in a copy field beside the image, and the receiving end accepts either. See
``sso_core/enrollment.py`` for what the string contains.

**No new dependency.** ``opencv-python-headless`` is pinned identically on every
host (``portal/*/requirements.txt``) because ``socialhub/captcha.py`` needs it and
socialhub is a CORE_APP, so ``cv2.QRCodeEncoder`` and ``cv2.QRCodeDetector`` are
already present. Only faros pins the ``qrcode`` package, for ``toto.nomad``;
adding it to two more hosts to draw one image would be waste. The data-URI idiom
below is captcha.py's, deliberately (``captcha.py:107-110``).

**The one trap.** ``cv2.QRCodeEncoder.encode()`` returns a bare one-pixel-per-
module array with **no quiet zone**, and feeding that straight back to the
detector returns ``""`` — the encoder's own output does not survive its own
decoder. It needs upscaling and a white border first. Both are done here, and
``tests`` round-trips through them so the trap cannot come back.
"""
from __future__ import annotations

import base64

# The white border every QR needs, in modules. Four is the spec minimum.
QUIET_ZONE_MODULES = 4
# Pixels per module. 8 gives a ~500px image for our payload — big enough to
# photograph off a screen, small enough to inline as a data URI.
DEFAULT_SCALE = 8


class QRError(RuntimeError):
    """A QR image could not be produced or read."""


def render_data_uri(text: str, *, scale: int = DEFAULT_SCALE) -> str:
    """``text`` as a PNG ``data:`` URI, ready for an ``<img src>``.

    Returns the same shape ``socialhub.captcha.generate_code_captcha`` does, for
    the same reason: an admin page can inline it with no media file, no extra
    view and no URL to authorise.
    """
    import cv2
    import numpy as np  # noqa: F401  (cv2 needs it loaded; kept explicit)

    if not text:
        raise QRError("Refusing to render an empty QR code.")

    try:
        matrix = cv2.QRCodeEncoder.create().encode(text)
    except Exception as exc:  # noqa: BLE001 — cv2 raises bare exceptions
        raise QRError(f"Could not encode a QR code: {exc}") from exc

    image = _with_quiet_zone(_upscale(matrix, scale), scale)

    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise QRError("Failed to encode the QR code as PNG.")
    return "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode("ascii")


def read(image_bytes: bytes) -> str:
    """The text in an uploaded QR image, or ``QRError``.

    Deliberately tolerant about the input: an operator photographs a screen or
    saves a screenshot, so this sees JPEG and PNG at any size and at an angle.
    ``detectAndDecode`` handles the perspective correction; what it cannot do is
    read an image with no QR in it, which is the common mistake and gets a
    message saying so.

    **cv2's detector is heuristic and intermittently misses a perfectly clean
    code** — empirically about one random payload in forty, which is a flaky
    gate and a real "it didn't work, try again" for an operator. So a first miss
    is not believed: the image is retried upscaled and Otsu-thresholded, which
    gives the detector more to work with and resolves the miss deterministically.
    Only when every variant fails is it reported as unreadable.
    """
    import cv2
    import numpy as np

    if not image_bytes:
        raise QRError("No image was uploaded.")

    array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise QRError("That file is not an image this server can read.")

    text, found_any = _decode_resilient(image)
    if text:
        return text
    if found_any:
        raise QRError("A QR code was found but could not be read — try a sharper picture.")
    raise QRError("No QR code was found in that image.")


# -- internals ------------------------------------------------------------


def _decode_resilient(image):
    """Return ``(text, found_any_qr)`` trying progressively cleaned-up copies.

    A given payload either decodes on the first try or (rarely) not at all with the
    raw image — the miss is image-dependent, not random per call, so retrying the
    same pixels is pointless. Upscaling gives the detector more pixels per module,
    and Otsu forces a crisp black/white edge for a soft screenshot; between them
    they recover the codes the raw pass drops. A genuine "no QR here" falls through
    every variant and still ends at the honest error.
    """
    import cv2

    variants = [image]
    for factor in (2, 4):
        variants.append(
            cv2.resize(image, None, fx=factor, fy=factor, interpolation=cv2.INTER_NEAREST)
        )
    try:
        _thr, otsu = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append(otsu)
        variants.append(
            cv2.resize(otsu, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
        )
    except Exception:                       # noqa: BLE001 — thresholding is a bonus, not required
        pass

    detector = cv2.QRCodeDetector()
    found_any = False
    for variant in variants:
        try:
            text, points, _ = detector.detectAndDecode(variant)
        except Exception:                   # noqa: BLE001 — try the next variant
            continue
        if text:
            return text, True
        if points is not None:
            found_any = True
    return "", found_any


def _upscale(matrix, scale: int):
    import cv2

    return cv2.resize(matrix, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)


def _with_quiet_zone(image, scale: int):
    """The white border. Without it the result is unreadable — see the module docstring."""
    import cv2

    pad = QUIET_ZONE_MODULES * scale
    return cv2.copyMakeBorder(
        image, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255,
    )
