"""
Visual verification-code helpers for the membership application flow.

The verification code is never emailed: it is rendered as a distorted
CAPTCHA-style image the applicant retypes. This only needs to keep bots
out — the reference/endorsement step is what actually gates membership.

Drawn with Pillow since 2026-10-01 (37c.30). OpenCV and numpy drew it until
then: 235 MB of the image, for this one picture (and an encryption library
OpenCV bundles that no longer gets security fixes), where Pillow is
installed anyway. The picture looks different; what makes it work is kept:
paper-like noise, faint decoy letters and lines behind the code, each
character on its own slant and height, a wave through the whole, and a
translucent band across it.
"""

import base64
import io
import math
import random
import string

from django.conf import settings

#: Each character's cell and the margin around the code, in pixels.
CHAR_W = 46
MARGIN = 28
#: How far the wave moves a row, in pixels, and how tall one swing is.
WAVE_AMPLITUDE = 3.0
WAVE_PERIOD = 13.0


def _font(size):
    """Pillow's own font (Aileron) at ``size`` pixels: the image needs no
    font file of its own, and every Pillow wheel carries FreeType."""
    from PIL import ImageFont

    return ImageFont.load_default(size=size)


def _background(rng, width, height):
    """Light paper with a faint grain of its own, so flat-colour OCR struggles."""
    from PIL import Image, ImageChops

    grain = Image.frombytes("L", (width, height), rng.randbytes(width * height))
    grain = grain.point([value % 18 for value in range(256)])
    paper = ImageChops.subtract(Image.new("L", (width, height), 244), grain)
    return Image.merge("RGB", (paper, paper, paper))


def _glyph(rng, ch, height):
    """One character as a mask at full strength, on its own size, place and
    slant, so the ink colour chosen for it can never drop it. Twice a cell
    wide, so a slanted character keeps its corners and may lean into its
    neighbours' cells."""
    from PIL import Image, ImageDraw

    font = _font(int(height * 0.56 * rng.uniform(0.92, 1.08)))
    mask = Image.new("L", (CHAR_W * 2, height), 0)
    draw = ImageDraw.Draw(mask)
    left, top, right, bottom = draw.textbbox((0, 0), ch, font=font, stroke_width=2)
    x = (CHAR_W * 2 - (right - left)) // 2 - left + rng.randint(-4, 4)
    y = (height - (bottom - top)) // 2 - top + rng.randint(-6, 6)
    draw.text((x, y), ch, fill=255, font=font, stroke_width=2, stroke_fill=255)
    return mask.rotate(rng.uniform(-15, 15), resample=Image.Resampling.BICUBIC)


def _wave(img):
    """Every row slid sideways along a sine, so no baseline is straight —
    drawn as thin strips of a mesh, which keeps the strokes smooth."""
    from PIL import Image

    width, height = img.size
    mesh = []
    for top in range(0, height, 2):
        bottom = min(top + 2, height)
        upper = WAVE_AMPLITUDE * math.sin(top / WAVE_PERIOD)
        lower = WAVE_AMPLITUDE * math.sin(bottom / WAVE_PERIOD)
        mesh.append(((0, top, width, bottom),
                     (upper, top, lower, bottom, width + lower, bottom, width + upper, top)))
    return img.transform(img.size, Image.Transform.MESH, mesh,
                         resample=Image.Resampling.BILINEAR, fillcolor=(236, 236, 236))


def generate_code_captcha(code, *, height=90, spurious_letters=None):
    """
    Render ``code`` as a slightly distorted image and return it as a
    ``data:image/png;base64,...`` URI suitable for an ``<img src>``.

    The characters are jittered, slanted and waved for mild distortion, a
    handful of faint spurious decoy letters are scattered behind them to
    confuse OCR bots, and a semi-transparent skewed red band is drawn across
    them.

    ``spurious_letters`` defaults to the ``SOCIALHUB_CAPTCHA_SPURIOUS_LETTERS``
    setting; the decoys are background noise, not part of the typed code.
    """
    from PIL import Image, ImageDraw

    if spurious_letters is None:
        spurious_letters = getattr(settings, "SOCIALHUB_CAPTCHA_SPURIOUS_LETTERS", 4)

    code = str(code)
    width = MARGIN * 2 + CHAR_W * max(len(code), 1)
    # The same code draws the same picture in a process, so asking again
    # gives a script no second, differently noisy copy to average.
    rng = random.Random(abs(hash(code)) % (2**32))
    img = _background(rng, width, height)
    draw = ImageDraw.Draw(img)

    # A couple of faint distractor lines.
    for _ in range(3):
        shade = rng.randint(170, 210)
        draw.line([(rng.randrange(width), rng.randrange(height)),
                   (rng.randrange(width), rng.randrange(height))],
                  fill=(shade, shade, shade), width=1)

    # Faint spurious decoy letters scattered behind the code. They are light
    # grey so a human reads past them but OCR picks them up as garbage.
    for _ in range(max(int(spurious_letters), 0)):
        ch = rng.choice(string.ascii_uppercase)
        shade = rng.randint(195, 220)
        font = _font(int(height * rng.uniform(0.32, 0.44)))
        x = rng.randint(MARGIN // 2, max(width - MARGIN, MARGIN // 2 + 1))
        y = rng.randint(int(height * 0.15), int(height * 0.55))
        draw.text((x, y), ch, fill=(shade, shade, shade), font=font)

    # Each character in dark ink through its own mask, centred on its cell.
    for i, ch in enumerate(code):
        ink = rng.randint(20, 60)
        x0 = MARGIN + i * CHAR_W - CHAR_W // 2
        img.paste((ink, ink, ink), (x0, 0, x0 + CHAR_W * 2, height), _glyph(rng, ch, height))

    # One thin, lighter stroke through the characters: a reader looks past
    # it, and a script that cuts the picture into characters at the gaps
    # between dark shapes finds them joined.
    shade = rng.randint(80, 115)
    phase, rise = rng.uniform(0, math.tau), rng.uniform(-0.12, 0.12)
    mid = height * rng.uniform(0.45, 0.6)
    draw.line([(x, mid + rise * (x - width / 2) + 7 * math.sin(x / 23 + phase))
               for x in range(MARGIN // 2, width - MARGIN // 2, 3)],
              fill=(shade, shade, shade), width=2, joint="curve")

    img = _wave(img)

    # Semi-transparent skewed red band across the characters.
    band_cy = int(height * 0.55)
    band_h = int(height * 0.34)
    skew = int(height * 0.30)
    overlay = img.copy()
    ImageDraw.Draw(overlay).polygon([
        (0, band_cy - band_h // 2 - skew),
        (width, band_cy - band_h // 2 + skew),
        (width, band_cy + band_h // 2 + skew),
        (0, band_cy + band_h // 2 - skew),
    ], fill=(210, 40, 40))
    img = Image.blend(img, overlay, 0.38)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
