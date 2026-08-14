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


def _outcome_sentence(decision) -> str:
    from .models import Outcome

    if decision.outcome == Outcome.WINNER:
        return _("Outcome: %(label)s") % {"label": decision.winner_label}
    if decision.outcome == Outcome.TIE:
        return _("Outcome: tie — no single leading option")
    return _("Outcome: no ballots were cast")


def _turnout_text(decision) -> str:
    if decision.turnout is None:
        return _("unknown")
    return f"{decision.turnout * 100:.0f}%"


def vote_result_pdf(question, decision) -> bytes:
    """One formal vote's recorded decision, as a document.

    Everything comes from the Decision row and its ``content`` snapshot —
    never from a live tally, so the paper says what was decided even if the
    database has since moved on.
    """
    A4, cm, simpleSplit, canvas = _reportlab()

    content = decision.content or {}
    window = content.get("window", {})
    proposer = content.get("proposer") or {}

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    page = _Page(c, A4, cm)

    page.line(question.title, font="Helvetica-Bold", size=16, dy=24)
    page.line(_("Formal vote — decision record"), size=9, dy=16)

    page.line(_("Proposer: %(name)s") % {
        "name": proposer.get("username") or _("(removed account)")})
    page.line(_("Voting window: %(opens)s — %(closes)s") % {
        "opens": (window.get("opens_at") or "")[:16].replace("T", " "),
        "closes": ((window.get("closes_at") or "")[:16].replace("T", " ")
                   or _("open until closed"))})
    page.line(_("Electorate: %(key)s (%(size)s eligible)") % {
        "key": decision.electorate_key or _("unspecified"),
        "size": decision.electorate_size or _("unknown")})
    page.line(_("Decided: %(when)s") % {
        "when": decision.decided_at.strftime("%Y-%m-%d %H:%M")})
    who = decision.decided_by
    page.line(_("Recorded by: %(who)s") % {
        "who": who.get_username() if who else _("the deadline (automatic)")})
    page.gap(8)

    if question.question_text:
        page.wrapped(question.question_text, simpleSplit,
                     font="Helvetica-Bold", size=11)
    if question.body:
        page.gap(4)
        page.wrapped(question.body, simpleSplit, size=9)
    page.gap(10)

    page.line(_outcome_sentence(decision), font="Helvetica-Bold", size=12,
              dy=20)

    # -- the tally table ----------------------------------------------------
    col_label, col_ballots, col_weight, col_share = (
        2 * cm, 11 * cm, 13.5 * cm, 16 * cm)
    page.need(40)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(col_label, page.y, _("Option"))
    c.drawString(col_ballots, page.y, _("Ballots"))
    c.drawString(col_weight, page.y, _("Weight"))
    c.drawString(col_share, page.y, _("Share"))
    page.y -= 14

    total_weight = decision.total_weight or 0
    for row in content.get("tally", []):
        page.need(12)
        c.setFont("Helvetica", 9)
        c.drawString(col_label, page.y, str(row.get("label", ""))[:48])
        c.drawRightString(col_ballots + 1 * cm, page.y,
                          str(row.get("ballots", 0)))
        c.drawRightString(col_weight + 1 * cm, page.y,
                          str(row.get("weight", 0)))
        share = (f"{row.get('weight', 0) / total_weight * 100:.1f}%"
                 if total_weight else "—")
        c.drawRightString(col_share + 1 * cm, page.y, share)
        page.y -= 12

    page.gap(6)
    page.line(_("Total: %(ballots)s ballots, weight %(weight)s — "
                "turnout %(turnout)s") % {
        "ballots": decision.total_ballots, "weight": decision.total_weight,
        "turnout": _turnout_text(decision)}, font="Helvetica-Bold", size=9)
    page.gap(12)

    # -- the audit appendix --------------------------------------------------
    ballots = content.get("ballots", [])
    if ballots:
        page.line(_("Ballot record (%(count)s)") % {"count": len(ballots)},
                  font="Helvetica-Bold", size=11, dy=16)
        for entry in ballots:
            page.need(11)
            c.setFont("Helvetica", 8)
            c.drawString(2 * cm, page.y, str(entry.get("voter_username", "")))
            c.drawString(8 * cm, page.y, str(entry.get("choice_label", ""))[:32])
            c.drawRightString(14.5 * cm, page.y,
                              _("weight %(w)s") % {"w": entry.get("weight", "")})
            c.drawString(15 * cm, page.y,
                         str(entry.get("cast_at", ""))[:16].replace("T", " "))
            page.y -= 11

    c.showPage()
    c.save()
    return buf.getvalue()


def ledger_pdf(decisions, *, filters: dict | None = None) -> bytes:
    """The filtered ledger as a listing — one block per decision."""
    A4, cm, simpleSplit, canvas = _reportlab()

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    page = _Page(c, A4, cm)

    page.line(_("Decision ledger"), font="Helvetica-Bold", size=16, dy=22)
    active = ", ".join(f"{k}={v}" for k, v in (filters or {}).items() if v)
    page.line(_("Filters: %(active)s") % {"active": active or _("none")},
              size=9, dy=18)

    for decision in decisions:
        page.need(46)
        page.line(f"{decision.decided_at.strftime('%Y-%m-%d')} — "
                  f"{decision.title}", font="Helvetica-Bold", size=11)
        page.line(_outcome_sentence(decision), size=9)
        page.line(_("Electorate %(key)s — %(ballots)s ballots, "
                    "weight %(weight)s, turnout %(turnout)s") % {
            "key": decision.electorate_key or _("unspecified"),
            "ballots": decision.total_ballots,
            "weight": decision.total_weight,
            "turnout": _turnout_text(decision)}, size=9)
        page.gap(8)

    if not decisions:
        page.line(_("No decisions match these filters."), size=10)

    c.showPage()
    c.save()
    return buf.getvalue()
