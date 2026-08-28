"""Is this really a page we can read, and are we allowed to try?

Three questions in a fixed order, cheapest and most fundamental first, so that
nothing expensive happens on a file that was never going to work:

1. **Size**, before a single byte is decoded. This is the antivirus engine's own
   rule — "too big to check" must be a refusal, not a silent skip that looks
   like a pass — and here it is also the difference between refusing an upload
   and spooling 200 MB to disk to find out we did not want it.
2. **The declared type**, from the name and the browser's content type, using
   the vault's own `VaultFile.detect_type`. Not a second copy of that table.
3. **The actual type**, by really decoding. A file called `scan.png` whose bytes
   are a PDF is refused rather than quietly read, and that agreement between
   what a file claims and what it is IS the check — a name is not evidence.

`python-magic` is deliberately not used. libmagic appears nowhere in this suite,
and a real decode by the library that will actually read the file is a better
answer than a signature table's opinion: it proves the thing we are about to do
can be done.
"""

from __future__ import annotations

import dataclasses

from django.utils.translation import gettext as _

from toto.ocr import engine

#: The two things there is any point pointing a page reader at.
SUPPORTED = ("image", "pdf")


@dataclasses.dataclass(frozen=True)
class Inspection:
    ok: bool
    kind: str = ""
    page_count: int = 0
    reason: str = ""
    message: str = ""
    #: True when the antivirus actually looked. False is not a failure — a PDF
    #: over the scanner's own ceiling is passed through and SAID to be
    #: unscanned, because "checked and clean" and "never checked" must not
    #: look the same to whoever reads the page.
    scanned: bool = False
    scan_note: str = ""

    @classmethod
    def refuse(cls, reason: str, message: str) -> "Inspection":
        return cls(ok=False, reason=reason, message=message)


def _mb(n: int) -> int:
    return max(1, round(n / (1024 * 1024)))


def inspect_upload(uploaded, *, max_bytes: int, page_cap: int) -> Inspection:
    """Screen a freshly uploaded file. `uploaded` is an UploadedFile."""
    size = int(getattr(uploaded, "size", 0) or 0)
    name = getattr(uploaded, "name", "") or ""
    declared = getattr(uploaded, "content_type", "") or ""
    return _inspect(uploaded, size=size, name=name, declared=declared,
                    max_bytes=max_bytes, page_cap=page_cap)


def inspect_group(uploads, *, max_bytes: int, page_cap: int) -> Inspection:
    """Screen a GROUP of images submitted as one job — one page each.

    The size check is on the SUM, not on each file. Per-file would make the cap
    trivially evadable: sixty-four 1 MB images is the same 64 MB of disk and the
    same 64 pages of work as one 64 MB book.
    """
    total = sum(int(getattr(u, "size", 0) or 0) for u in uploads)
    if total > max_bytes:
        return Inspection.refuse(
            "too-large",
            _("Those files come to %(actual)s MB together. The limit here is "
              "%(limit)s MB.") % {"actual": _mb(total), "limit": _mb(max_bytes)})
    if len(uploads) > page_cap:
        return Inspection.refuse(
            "too-many-pages",
            _("That is %(pages)s images. The limit here is %(cap)s.")
            % {"pages": len(uploads), "cap": page_cap})

    # Every file is inspected: one bad image in a group of forty should be
    # named now, not discovered as a failed page twenty minutes later.
    for upload in uploads:
        one = _inspect(upload, size=int(getattr(upload, "size", 0) or 0),
                       name=getattr(upload, "name", "") or "",
                       declared=getattr(upload, "content_type", "") or "",
                       max_bytes=max_bytes, page_cap=page_cap)
        if not one.ok:
            return Inspection.refuse(
                one.reason,
                _("%(name)s: %(why)s") % {"name": getattr(upload, "name", "?"),
                                          "why": one.message})
        if one.kind != "image":
            return Inspection.refuse(
                "unsupported",
                _("Send one PDF, or several images — not a mixture."))
    return Inspection(ok=True, kind="image", page_count=len(uploads))


def inspect_vault_file(vault_file, *, max_bytes: int, page_cap: int) -> Inspection:
    """Screen a file already in the vault, reached through the wand."""
    size = int(getattr(vault_file, "file_size_bytes", 0) or 0)
    try:
        handle = vault_file.file.storage.open(vault_file.file.name, "rb")
    except Exception:  # noqa: BLE001
        return Inspection.refuse("unreadable",
                                 _("That file could not be opened."))
    with handle:
        if not size:
            handle.seek(0, 2)
            size = handle.tell()
        return _inspect(handle, size=size, name=vault_file.title or "",
                        declared="", max_bytes=max_bytes, page_cap=page_cap,
                        known_type=vault_file.file_type)


def _inspect(fh, *, size, name, declared, max_bytes, page_cap,
             known_type: str = "") -> Inspection:
    # 1 — size, before anything is decoded.
    if size > max_bytes:
        return Inspection.refuse(
            "too-large",
            _("That file is %(actual)s MB. The limit here is %(limit)s MB.")
            % {"actual": _mb(size), "limit": _mb(max_bytes)})
    if not size:
        return Inspection.refuse("empty", _("That file is empty."))

    # 2 — what it claims to be.
    from toto.vault.models import VaultFile

    kind = known_type or VaultFile.detect_type(declared, name)
    if kind not in SUPPORTED:
        return Inspection.refuse(
            "unsupported",
            _("Text recognition reads images and PDFs. That looks like "
              "a %(kind)s file.") % {"kind": kind or _("different kind of")})

    # 3 — what it actually is.
    if kind == "image":
        return _inspect_image(fh)
    return _inspect_pdf(fh, size=size, name=name, page_cap=page_cap)


def _inspect_image(fh) -> Inspection:
    from PIL import Image, UnidentifiedImageError

    try:
        fh.seek(0)
        image = Image.open(fh)
        image.verify()
    except UnidentifiedImageError:
        return Inspection.refuse(
            "wrong-shape",
            _("That file is named like an image but is not one."))
    except Image.DecompressionBombError:
        return Inspection.refuse(
            "wrong-shape",
            _("That image is too large to open safely."))
    except Exception as exc:  # noqa: BLE001
        return Inspection.refuse(
            "wrong-shape",
            _("That image could not be read (%(detail)s).") % {"detail": exc})
    # `verify()` leaves the object unusable by design, so anything else about
    # the image has to come from a fresh open. There is nothing else we need.
    return Inspection(ok=True, kind="image", page_count=1, scanned=False,
                      scan_note="")


def _inspect_pdf(fh, *, size, name, page_cap) -> Inspection:
    import os
    import tempfile

    if not engine.pdf_support_available():
        return Inspection.refuse(
            "unsupported",
            _("This server cannot read PDFs — only images."))

    # pdfinfo needs a path. The copy is the price of asking a subprocess, and
    # it is bounded by the size check above.
    tmp_dir = tempfile.mkdtemp(prefix="ocr-inspect-")
    tmp_path = os.path.join(tmp_dir, "input.pdf")
    try:
        fh.seek(0)
        raw = fh.read()
        with open(tmp_path, "wb") as out:
            out.write(raw)
        try:
            pages = engine.page_count(tmp_path)
        except Exception as exc:  # noqa: BLE001
            return Inspection.refuse(
                "wrong-shape",
                _("That is not a PDF this server can read (%(detail)s).")
                % {"detail": str(exc)[:200]})
        if pages < 1:
            return Inspection.refuse("wrong-shape",
                                     _("That PDF has no pages."))
        if pages > page_cap:
            return Inspection.refuse(
                "too-many-pages",
                _("That document has %(pages)s pages. The limit here is "
                  "%(cap)s.") % {"pages": pages, "cap": page_cap})

        scanned, note = _screen_pdf(raw, name)
        if scanned is None:
            return Inspection.refuse("active-content", note)
        return Inspection(ok=True, kind="pdf", page_count=pages,
                          scanned=scanned, scan_note=note)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass


def _screen_pdf(raw: bytes, name: str):
    """Run the antivirus over a PDF, respecting its own size ceiling.

    Returns `(scanned, note)`, or `(None, message)` for a refusal.

    The ceiling matters more here than anywhere else on the platform. The
    scanner refuses anything over `scan_max_mb` — 10 MB by default — as
    "wrong-shape" before decoding, and an ordinary scanned book is about 19 MB.
    Handing it straight to `scanning.scan` would refuse precisely the files this
    feature exists for, with a message about the wrong subject.

    So: ask the cap first, and above it pass the file through UNSCANNED and say
    so. That is defensible here and would not be everywhere — what the PDF
    scanner names is active content (`/JavaScript`, `/OpenAction`, `/Launch`),
    which matters when a viewer opens a file. This pipeline hands the PDF to
    `pdftoppm`, which has no JavaScript engine and executes nothing.
    Rasterisation is not the door that scanner guards. Within the cap a refusal
    is still a hard refusal.
    """
    from toto.vault import scanning

    if not scanning.should_scan(None, "pdf", door="ocr"):
        return False, ""
    cap = scanning.scan_size_cap_bytes()
    if cap is not None and len(raw) > cap:
        return False, _(
            "Too large for the virus scanner (over %(cap)s MB), so it was not "
            "screened. Reading a page runs no code from the file."
        ) % {"cap": _mb(cap)}
    verdict = scanning.scan(raw, file_type="pdf", filename=name)
    if not verdict.ok:
        return None, _(
            "That PDF was refused by the virus scanner (%(reason)s)."
        ) % {"reason": verdict.reason or "refused"}
    return bool(verdict.scanned), ""
