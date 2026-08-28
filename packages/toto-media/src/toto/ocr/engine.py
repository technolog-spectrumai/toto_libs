"""Tesseract and poppler, called directly.

Until 1.51 this ran in an Anastasia Compute Gear. It does not any more, and the
reasoning is the one the zenobia Dockerfile already makes about WeasyPrint:
reading a page is ordinary Python over a ~20 MB apt layer — no reserved CPU, no
mounted scratch, nothing to isolate — and making somebody book a Gear to read a
scan is a worse product than carrying the layer.

**Every import of pytesseract lives inside a function.** The suite's own test
environment has Pillow but not pytesseract, so a module-level import would break
`toto.ocr` for anybody running the library's tests — the same trap the vendored
tree already hit with opencv.

**poppler is a subprocess, not a library.** `pdftoppm` renders one page at a
time into a temp directory. A malformed PDF that crashes an in-process renderer
would take the whole Celery worker down with it, and the entire design of this
feature is that one page can fail alone.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

#: A tesseract language spec: "eng", "pol", "eng+pol". Nothing else may reach a
#: command line assembled from it. Copied from
#: toto.anastasia.families rather than imported: this app deliberately no longer
#: depends on the compute app, and one regex is a smaller price than that edge.
SAFE_LANG = re.compile(r"^[a-z]{3}(\+[a-z]{3}){0,3}$")

#: Orientation and script detection data. It ships with tesseract, it is not a
#: language, and offering it produces empty text that reads like a bug.
_NOT_A_LANGUAGE = {"osd"}

#: Render resolution. 300 DPI is what a scan is normally read at; more is
#: linearly more pixels and more seconds for no accuracy on a 150 DPI source.
RENDER_DPI = 300

#: Bounds on the two external programs. Both sit far below the per-page Celery
#: soft limit so a wedged page fails as THAT PAGE's error, with a `finally` that
#: still runs, rather than being killed with the run left mid-flight.
RENDER_TIMEOUT = 120
READ_TIMEOUT = 180

_LANGS_CACHE: list | None = None
_HAVE_CACHE: dict = {}


class EngineUnavailable(RuntimeError):
    """No tesseract on this host. A refusal with a name, not an ImportError."""


def have(program: str) -> bool:
    """Is this binary on PATH? Cached — the answer cannot change mid-process."""
    if program not in _HAVE_CACHE:
        _HAVE_CACHE[program] = shutil.which(program) is not None
    return _HAVE_CACHE[program]


def tesseract_available() -> bool:
    return have("tesseract")


def pdf_support_available() -> bool:
    """Reading a PDF needs poppler as well as tesseract."""
    return have("tesseract") and have("pdftoppm") and have("pdfinfo")


def available_languages() -> list:
    """The language packs actually installed, best first.

    Asked of the binary, never of a setting. TESSERACT_LANGS is a BUILD argument:
    a config edited after the image was built would advertise `pol` on an
    English-only image, and every Polish scan would fail with a tesseract error
    the user cannot act on. Only the binary knows what is really there.

    Falls back to TESSERACT_LANGS only when the binary cannot be asked at all,
    and to `eng` when even that is unset — never to a hardcoded menu.
    """
    global _LANGS_CACHE
    if _LANGS_CACHE is not None:
        return list(_LANGS_CACHE)

    langs: list = []
    if have("tesseract"):
        try:
            out = subprocess.run(["tesseract", "--list-langs"],
                                 capture_output=True, text=True, timeout=5)
            # The first line is a header ("List of available languages ...").
            for line in (out.stdout or "").splitlines()[1:]:
                code = line.strip()
                if code and code not in _NOT_A_LANGUAGE and SAFE_LANG.match(code):
                    langs.append(code)
        except Exception:  # noqa: BLE001 — an unaskable binary is not an error
            langs = []
    if not langs:
        langs = [c for c in (os.environ.get("TESSERACT_LANGS") or "eng").split()
                 if c not in _NOT_A_LANGUAGE and SAFE_LANG.match(c)]
    if not langs:
        langs = ["eng"]

    # English first where present; it is the common case and the default.
    langs = sorted(set(langs), key=lambda c: (c != "eng", c))
    _LANGS_CACHE = langs
    return list(langs)


#: Enough ISO-639-2 for the packs a host is likely to install. A bare code is a
#: better answer than a wrong name, so anything unlisted falls through to itself.
_LANGUAGE_NAMES = {
    "eng": "English", "pol": "Polish", "deu": "German", "fra": "French",
    "spa": "Spanish", "ita": "Italian", "por": "Portuguese", "nld": "Dutch",
    "ces": "Czech", "slk": "Slovak", "ukr": "Ukrainian", "rus": "Russian",
    "swe": "Swedish", "nor": "Norwegian", "dan": "Danish", "fin": "Finnish",
    "hun": "Hungarian", "ron": "Romanian", "tur": "Turkish", "ell": "Greek",
    "lit": "Lithuanian", "lav": "Latvian", "est": "Estonian",
}


def language_label(code: str) -> str:
    name = _LANGUAGE_NAMES.get(code)
    return f"{name} ({code})" if name else code


def language_choices() -> list:
    """(code, label) pairs for the picker."""
    return [(code, language_label(code)) for code in available_languages()]


def is_offered(code: str) -> bool:
    return code in available_languages()


def page_count(pdf_path: str) -> int:
    """How many pages, without rendering any of them.

    `pdfinfo` answers in milliseconds even for a 300-page book, which is why
    this is safe to do in the request that accepts the upload — the denominator
    has to be frozen before the first task is queued.
    """
    out = subprocess.run(["pdfinfo", pdf_path],
                         capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise ValueError((out.stderr or "pdfinfo failed").strip())
    for line in (out.stdout or "").splitlines():
        if line.lower().startswith("pages:"):
            return int(line.split(":", 1)[1].strip())
    raise ValueError("pdfinfo reported no page count")


def render_pdf_page(pdf_path: str, number: int, out_dir: str) -> str:
    """Rasterise ONE page of a PDF and return the image path.

    `-f N -l N` is what keeps this cheap: extracting page 250 of a 300-page book
    costs about one page's work, not the document's. Rendering the whole book up
    front would be ~450 MB of scratch and minutes before the first character
    appears. Do not "optimise" this into a single pass — the per-page process is
    also what makes a page fail alone.
    """
    stem = os.path.join(out_dir, "page")
    out = subprocess.run(
        ["pdftoppm", "-f", str(number), "-l", str(number),
         "-r", str(RENDER_DPI), "-gray", "-png", "-singlefile",
         pdf_path, stem],
        capture_output=True, text=True, timeout=RENDER_TIMEOUT)
    path = f"{stem}.png"
    if out.returncode != 0 or not os.path.exists(path):
        raise ValueError((out.stderr or "").strip()
                         or f"page {number} could not be rendered")
    return path


def read_image(image_path: str, language: str) -> str:
    """The text of one image, as plain text in reading order."""
    if not have("tesseract"):
        raise EngineUnavailable(
            "Tesseract is not installed on this server. A build that offers "
            "text recognition sets BUILD_OCR=1, which installs it."
        )
    if not SAFE_LANG.match(language or ""):
        raise ValueError(f"{language!r} is not a language code")
    out = subprocess.run(
        ["tesseract", image_path, "stdout", "-l", language],
        capture_output=True, text=True, timeout=READ_TIMEOUT)
    if out.returncode != 0:
        raise ValueError((out.stderr or "").strip() or "tesseract failed")
    return (out.stdout or "").strip()


def _reset_caches_for_tests() -> None:
    global _LANGS_CACHE
    _LANGS_CACHE = None
    _HAVE_CACHE.clear()
