"""A decision on paper: ReportLab, Helvetica, and nothing clever.

The notarius idiom (``sign_pdf.py``): low-level canvas, A4 with a ``cm``
cursor, manual page breaks. Deliberately not WeasyPrint — a results table
needs no HTML engine, and reportlab is the one PDF wheel zenobia always
installs.

**reportlab is imported inside the functions, never at module level.** polls
ships in toto-base, and toto-base does not declare reportlab: on a build
without the wheel this module must import cleanly and refuse by name
(:class:`PdfUnavailable` → the views answer 503 with a sentence), the same
shape cyprian uses for WeasyPrint.
"""

from __future__ import annotations

import io

from django.utils.translation import gettext as _


class PdfUnavailable(RuntimeError):
    """reportlab is not installed on this deployment."""

    def __init__(self):
        super().__init__(_(
            "PDF export needs the reportlab library, which this deployment "
            "does not install."))


def is_available() -> bool:
    try:
        import reportlab  # noqa: F401
    except ImportError:
        return False
    return True


def _reportlab():
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.lib.utils import simpleSplit
        from reportlab.pdfgen import canvas
    except ImportError as exc:
        raise PdfUnavailable() from exc
    return A4, cm, simpleSplit, canvas


class _Page:
    """The cursor, the margin, and the page break — in one place.

    Every notarius-style builder repeats the ``if y < margin: showPage()``
    dance inline; two builders in one module earn the tiny helper.
    """

    def __init__(self, c, A4, cm):
        self.c = c
        self.cm = cm
        self.width, self.height = A4
        self.y = self.height - 2.5 * cm

    def need(self, room):
        if self.y < room + 2 * self.cm:
            self.c.showPage()
            self.y = self.height - 2.5 * self.cm

    def line(self, text, *, font="Helvetica", size=10, dy=None, x=None):
        self.need(size)
        self.c.setFont(font, size)
        self.c.drawString(x if x is not None else 2 * self.cm, self.y, text)
        self.y -= dy if dy is not None else (size + 4)

    def gap(self, dy):
        self.y -= dy

    def wrapped(self, text, simpleSplit, *, font="Helvetica", size=10,
                width=None):
        max_width = width or (self.width - 4 * self.cm)
        for row in simpleSplit(text, font, size, max_width):
            self.line(row, font=font, size=size)


def certificate_pdf(certificate) -> bytes:
    """One attempt's result, as a document somebody can hold up.

    Rendered from the certificate's frozen ``content``, never from live rows
    — the paper keeps saying what was true when it was issued, however the
    quiz has been edited since.
    """
    A4, cm, simpleSplit, canvas = _reportlab()

    content = certificate.content or {}
    participant = content.get("participant") or {}
    quiz = content.get("quiz") or {}
    attempt = content.get("attempt") or {}
    status = content.get("status", "")

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    # A certificate composes on the page rather than running down it.
    c.setFont("Helvetica-Bold", 22)
    if status == "passed":
        heading = _("Certificate of Achievement")
    elif status == "completed":
        heading = _("Certificate of Completion")
    else:
        heading = _("Statement of Result")
    c.drawCentredString(width / 2, height - 4 * cm, heading)

    c.setFont("Helvetica", 11)
    c.drawCentredString(width / 2, height - 5.2 * cm, _("This certifies that"))
    c.setFont("Helvetica-Bold", 18)
    name = participant.get("full_name") or participant.get("username") or ""
    c.drawCentredString(width / 2, height - 6.4 * cm, name)

    c.setFont("Helvetica", 11)
    c.drawCentredString(width / 2, height - 7.6 * cm, _("took the quiz"))
    c.setFont("Helvetica-Bold", 14)
    for i, line in enumerate(simpleSplit(quiz.get("title", ""),
                                         "Helvetica-Bold", 14, width - 6 * cm)):
        c.drawCentredString(width / 2, height - (8.6 + i * 0.7) * cm, line)

    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(width / 2, height - 11 * cm, _(
        "Result: %(score)s / %(max)s — %(percent)s%%") % {
        "score": content.get("score", 0), "max": content.get("max_score", 0),
        "percent": content.get("percent", 0)})
    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(width / 2, height - 12 * cm, {
        "passed": _("PASSED"), "failed": _("NOT PASSED"),
        "completed": _("COMPLETED"),
    }.get(status, status.upper()))
    if quiz.get("pass_mark") is not None:
        c.setFont("Helvetica", 9)
        c.drawCentredString(width / 2, height - 12.7 * cm, _(
            "Pass mark: %(mark)s%%") % {"mark": quiz["pass_mark"]})

    c.setFont("Helvetica", 10)
    finished = (content.get("finished_at") or "")[:16].replace("T", " ")
    allowed = attempt.get("allowed")
    c.drawCentredString(width / 2, height - 14 * cm, _(
        "Completed %(when)s — attempt %(n)s%(of)s") % {
        "when": finished, "n": attempt.get("number", 1),
        "of": (_(" of %(allowed)s") % {"allowed": allowed}) if allowed else ""})

    c.setFont("Helvetica", 8)
    c.drawCentredString(width / 2, 2.5 * cm, _(
        "Certificate %(serial)s — issued %(when)s") % {
        "serial": certificate.serial,
        "when": (content.get("issued_at") or "")[:16].replace("T", " ")})

    c.showPage()
    c.save()
    return buf.getvalue()
