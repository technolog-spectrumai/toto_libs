"""
Visual verification-code helpers for the membership application flow.

The verification code is never emailed: it is rendered as a distorted
CAPTCHA-style image the applicant retypes. This only needs to keep bots
out — the reference/endorsement step is what actually gates membership.
"""

import base64
import string

from django.conf import settings

from toto.api.models import EmailService


def resolve_email_service(community):
    """
    Return the EmailService that should deliver this community's mail, or
    ``None`` when none is configured.

    Mirrors the previous ``community.email_service or default`` lookup but
    never raises: a missing ``default-email-service`` simply means "no email".
    """
    if community is not None and community.email_service is not None:
        return community.email_service
    return EmailService.objects.filter(name="default-email-service").first()


def generate_code_captcha(code, *, height=90, spurious_letters=None):
    """
    Render ``code`` as a slightly distorted image and return it as a
    ``data:image/png;base64,...`` URI suitable for an ``<img src>``.

    The digits are jittered and waved for mild distortion, a handful of
    faint spurious decoy letters are scattered behind them to confuse OCR
    bots, and a semi-transparent skewed red band is drawn across them.

    ``spurious_letters`` defaults to the ``SOCIALHUB_CAPTCHA_SPURIOUS_LETTERS``
    setting; the decoys are background noise, not part of the typed code.
    """
    import cv2
    import numpy as np

    if spurious_letters is None:
        spurious_letters = getattr(settings, "SOCIALHUB_CAPTCHA_SPURIOUS_LETTERS", 4)

    code = str(code)
    char_w = 46
    margin = 28
    width = margin * 2 + char_w * max(len(code), 1)

    # Light paper-like background with faint noise so flat-colour OCR struggles.
    rng = np.random.default_rng(abs(hash(code)) % (2**32))
    img = np.full((height, width, 3), 244, dtype=np.uint8)
    noise = rng.integers(0, 18, size=(height, width, 1), dtype=np.uint8)
    img = cv2.subtract(img, np.repeat(noise, 3, axis=2))

    # A couple of faint distractor lines.
    for _ in range(3):
        p1 = (int(rng.integers(0, width)), int(rng.integers(0, height)))
        p2 = (int(rng.integers(0, width)), int(rng.integers(0, height)))
        shade = int(rng.integers(170, 210))
        cv2.line(img, p1, p2, (shade, shade, shade), 1, cv2.LINE_AA)

    # Faint spurious decoy letters scattered behind the code. They are light
    # grey so a human reads past them but OCR picks them up as garbage.
    for _ in range(max(int(spurious_letters), 0)):
        ch = string.ascii_uppercase[int(rng.integers(0, 26))]
        scale = 1.2 + float(rng.uniform(-0.2, 0.4))
        x = int(rng.integers(margin // 2, max(width - margin // 2, margin // 2 + 1)))
        y = int(rng.integers(int(height * 0.45), int(height * 0.95)))
        shade = int(rng.integers(195, 220))
        cv2.putText(img, ch, (x, y), cv2.FONT_HERSHEY_TRIPLEX, scale,
                    (shade, shade, shade), 1, cv2.LINE_AA)

    # Draw each character with its own jitter, scale and slight rotation.
    font = cv2.FONT_HERSHEY_DUPLEX
    baseline_y = int(height * 0.68)
    for i, ch in enumerate(code):
        scale = 1.7 + float(rng.uniform(-0.15, 0.15))
        angle = float(rng.uniform(-18, 18))
        (tw, th), _ = cv2.getTextSize(ch, font, scale, 3)

        # Render the glyph as a single-channel alpha mask at full intensity so
        # the (independently chosen, possibly very dark) ink colour can't push
        # it below a threshold and drop the digit.
        alpha = np.zeros((height, char_w), dtype=np.uint8)
        ox = (char_w - tw) // 2 + int(rng.integers(-4, 5))
        oy = baseline_y + int(rng.integers(-6, 7))
        cv2.putText(alpha, ch, (ox, oy), font, scale, 255, 3, cv2.LINE_AA)

        rot = cv2.getRotationMatrix2D((char_w / 2, height / 2), angle, 1.0)
        alpha = cv2.warpAffine(alpha, rot, (char_w, height), borderValue=0)

        x0 = margin + i * char_w
        region = img[:, x0:x0 + char_w]
        a = (alpha.astype(np.float32) / 255.0)[:, :, None]
        ink = int(rng.integers(20, 60))
        region[:] = (region * (1.0 - a) + ink * a).astype(np.uint8)

    # Gentle horizontal wave so the baseline isn't perfectly straight.
    ys, xs = np.indices((height, width), dtype=np.float32)
    xs_shift = xs + 4.0 * np.sin(ys / 9.0)
    img = cv2.remap(img, xs_shift, ys, interpolation=cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_REFLECT)

    # Semi-transparent skewed red band across the digits.
    overlay = img.copy()
    band_cy = int(height * 0.55)
    band_h = int(height * 0.34)
    skew = int(height * 0.30)
    band = np.array([
        [0, band_cy - band_h // 2 - skew],
        [width, band_cy - band_h // 2 + skew],
        [width, band_cy + band_h // 2 + skew],
        [0, band_cy + band_h // 2 - skew],
    ], dtype=np.int32)
    cv2.fillPoly(overlay, [band], (40, 40, 210))  # BGR red
    cv2.addWeighted(overlay, 0.38, img, 0.62, 0, dst=img)

    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("Failed to encode CAPTCHA image.")
    return "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode("ascii")
