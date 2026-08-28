"""Building the HTML the Business Center hands to aralia.

**This app owns no renderer.** It turns a ledger, a vote or a meeting into one
HTML document and gives it to `toto.aralia`, which is the only thing on this
host that talks to WeasyPrint. That split is why there is no second PDF engine
to keep in step, and why aralia's SSRF refusal and its metering apply to
Business Center exports for free.

**No Django templates either.** These build strings. A `render_to_string` would
put the page's base template, its Alpine attributes and its stylesheet into a
document that is going to a printer, and then somebody would spend an afternoon
finding out why the PDF has a navigation bar in it. What goes to WeasyPrint is
written here, in full, with its own CSS.

Images are `data:` URIs because aralia refuses to fetch anything — see
`aralia/render.py::refuse_fetch`.
"""

from __future__ import annotations

from html import escape

#: Print CSS. Deliberately plain: this is a legal record, not a brochure.
BASE_CSS = """
  @page { size: A4; margin: 18mm 16mm; }
  body { font-family: "DejaVu Sans", sans-serif; font-size: 9.5pt; color: #111; }
  h1 { font-size: 17pt; margin: 0 0 2mm; }
  h2 { font-size: 12pt; margin: 6mm 0 2mm; border-bottom: 1px solid #999;
       padding-bottom: 1mm; }
  .meta { color: #444; font-size: 8.5pt; margin: 0 0 4mm; }
  table { border-collapse: collapse; width: 100%; margin-top: 2mm; }
  th, td { border: 1px solid #bbb; padding: 1.6mm 2mm; text-align: left;
           vertical-align: top; font-size: 8.5pt; }
  th { background: #eee; }
  .hash { font-family: "DejaVu Sans Mono", monospace; font-size: 7.5pt;
          word-break: break-all; }
  .verdict-ok { border: 1.5pt solid #157347; padding: 2.5mm; }
  .verdict-bad { border: 1.5pt solid #b02a37; padding: 2.5mm; }
  .qr { float: right; margin: 0 0 3mm 3mm; text-align: center; width: 42mm; }
  .qr img { width: 42mm; height: 42mm; }
  .qr .caption { font-size: 7pt; color: #444; margin-top: 1mm; }
  .payload { font-family: "DejaVu Sans Mono", monospace; font-size: 6.5pt;
             word-break: break-all; color: #333; margin-top: 2mm; }
"""


def document(title: str, body: str, *, css: str = "") -> str:
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{escape(title)}</title>"
        f"<style>{BASE_CSS}{css}</style></head>"
        f"<body>{body}</body></html>"
    )


def _qr_block(payload: str) -> str:
    """The verification QR, inline, plus its text.

    Both, on purpose: a phone camera is the convenient path and a printer is
    the reliable one, and the text is what somebody types in when the picture
    will not scan. `toto.core.qr` renders a `data:` URI — no media file, no
    extra view, and nothing for aralia to fetch.
    """
    from toto.core.qr import QRError, render_data_uri

    try:
        uri = render_data_uri(payload)
    except QRError:
        # A missing picture must not cost the document. The text alone still
        # verifies — it is the payload, and the image was only transport.
        return f"<div class='payload'>{escape(payload)}</div>"
    return (
        f"<div class='qr'><img src='{uri}' alt='Verification code'>"
        "<div class='caption'>Scan to verify this ledger</div></div>"
        f"<div class='payload'>{escape(payload)}</div>"
    )


def ledger_document(ledger, *, verification=None, checkpoint_payload="") -> str:
    """The chain, in reading order, with a verification QR when there is one."""
    from toto.ledger.services import chain as chain_service

    verification = verification or chain_service.verify(ledger)
    algorithm = chain_service.chain_algorithm(ledger)
    entries = ledger.entries.select_related("ledger").order_by("sequence")

    rows = "".join(
        "<tr>"
        f"<td>{entry.sequence}</td>"
        f"<td>{escape(entry.occurred_at.strftime('%Y-%m-%d %H:%M'))}</td>"
        f"<td>{'<strong>Genesis</strong> — seals the chain' if entry.is_genesis else escape(entry.source_ref or entry.source_type or '—')}</td>"
        f"<td>{escape(entry.actor_ref or '—')}</td>"
        f"<td class='hash'>{escape(entry.entry_hash)}</td>"
        "</tr>"
        for entry in entries
    )

    if verification.ok:
        verdict = (
            f"<p class='verdict-ok'><strong>Verified.</strong> All "
            f"{verification.checked} blocks re-hash to their stored values and "
            "each links to the one before it.</p>"
        )
    else:
        verdict = (
            "<p class='verdict-bad'><strong>This chain does not verify.</strong> "
            f"{escape(str(verification.checked))} block(s) verified, then block "
            f"{escape(str(verification.first_bad_sequence))} failed: "
            f"{escape(verification.detail)}</p>"
        )

    qr = _qr_block(checkpoint_payload) if checkpoint_payload else ""

    body = (
        f"{qr}"
        f"<h1>{escape(ledger.name)}</h1>"
        f"<p class='meta'>Chain {escape(str(ledger.uid))} · sealed with "
        f"{escape(algorithm)} · format {escape(ledger.format_version)} · "
        f"{ledger.entries.count()} blocks</p>"
        f"{verdict}"
        "<h2>Blocks, in order</h2>"
        "<table><thead><tr><th>#</th><th>Recorded</th><th>What</th>"
        "<th>By</th><th>Hash</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )
    return document(f"{ledger.name} — ledger", body)


def _roll_table(rows) -> str:
    body = "".join(
        "<tr>"
        f"<td>{escape(row['voter'])}</td>"
        f"<td>{escape(row.get('source', ''))}</td>"
        f"<td>{escape(row.get('status', ''))}</td>"
        f"<td>{escape(row.get('represented_by', '') or '—')}</td>"
        f"<td style='text-align:right'>{escape(row.get('weight', ''))}</td>"
        "</tr>"
        for row in rows
    )
    return (
        "<table><thead><tr><th>Voter</th><th>Source</th><th>Status</th>"
        "<th>Represented by</th><th>Weight</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def _ballot_table(rows) -> str:
    """Every ballot, with the name of who cast it.

    Superseded ballots are printed too, greyed. A record that showed only the
    final vote would be concealing that somebody changed their mind — and this
    document is the one people will read years from now.
    """
    body = "".join(
        f"<tr class='{'' if row.get('active', True) else 'superseded'}'>"
        f"<td>{escape(row['voter'])}</td>"
        f"<td>{escape(row['choice'])}</td>"
        f"<td style='text-align:right'>{escape(row['weight'])}</td>"
        f"<td>{escape((row.get('cast_at') or '')[:16].replace('T', ' '))}</td>"
        f"<td>{'counted' if row.get('active', True) else 'superseded'}</td>"
        "</tr>"
        for row in rows
    )
    return (
        "<table><thead><tr><th>Voter</th><th>Vote</th><th>Weight</th>"
        "<th>Cast</th><th>Counted</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def _result_table(result: dict) -> str:
    pairs = (
        ("For", result.get("for_weight")),
        ("Against", result.get("against_weight")),
        ("Abstain", result.get("abstain_weight")),
        ("Denominator", result.get("denominator_weight")),
        ("Achieved", f"{result.get('achieved_percent')}%"),
        ("Required", f"{result.get('majority_percent')}%"),
        ("Majority", "met" if result.get("majority_met") else "not met"),
        ("Quorum", "met" if result.get("quorum_met") else "not met"),
    )
    body = "".join(
        f"<tr><th style='width:38%'>{escape(label)}</th>"
        f"<td>{escape(str(value))}</td></tr>"
        for label, value in pairs
    )
    return f"<table><tbody>{body}</tbody></table>"


VOTE_CSS = """
  .superseded td { color: #777; text-decoration: line-through; }
  .outcome { font-size: 12pt; font-weight: bold; padding: 2.5mm;
             border: 1.5pt solid #111; margin: 3mm 0; }
  .resolution { border-left: 3pt solid #999; padding-left: 3mm; margin: 2mm 0 3mm; }
"""


def vote_document(proposition, *, payload=None) -> str:
    """One decision: what was voted on, under which rules, and by whom.

    Reads the FROZEN payload when there is one. A finalized decision must print
    what was decided, not a fresh count — those can differ if anything about
    the roll is later corrected, and the frozen copy is the one on the chain.
    """
    payload = payload or proposition.result_payload
    if not payload:
        from toto.voting.services.lifecycle import result_document

        payload = result_document(proposition)

    result = payload.get("result", {})
    proposition_facts = payload.get("proposition", {})
    meeting_facts = payload.get("meeting", {})
    rules = payload.get("configuration", {})

    attachment = ""
    if proposition_facts.get("attachment_ref"):
        attachment = (
            "<h2>Attachment</h2><table><tbody>"
            f"<tr><th style='width:38%'>Reference</th><td>"
            f"{escape(proposition_facts.get('attachment_label') or proposition_facts['attachment_ref'])}"
            "</td></tr>"
            f"<tr><th>Hash</th><td class='hash'>"
            f"{escape(proposition_facts.get('attachment_hash', ''))}</td></tr>"
            "</tbody></table>"
        )

    rule_rows = "".join(
        f"<tr><th style='width:38%'>{escape(key.replace('_', ' '))}</th>"
        f"<td>{escape(str(value))}</td></tr>"
        for key, value in sorted(rules.items())
    )

    body = (
        f"<h1>{escape(proposition_facts.get('title', proposition.title))}</h1>"
        f"<p class='meta'>{escape(meeting_facts.get('title', ''))} · "
        f"record date {escape(meeting_facts.get('record_date', ''))} · "
        f"held {escape((meeting_facts.get('held_at') or '')[:16].replace('T', ' '))}</p>"
        f"<p class='outcome'>Outcome: {escape(payload.get('outcome', ''))}</p>"
        "<h2>Resolution</h2>"
        f"<p class='resolution'>{escape(proposition_facts.get('resolution_text', ''))}</p>"
        + (f"<h2>Rationale</h2><p>{escape(proposition_facts['rationale'])}</p>"
           if proposition_facts.get("rationale") else "")
        + attachment
        + "<h2>Result</h2>" + _result_table(result)
        + f"<h2>Rules applied</h2><table><tbody>{rule_rows}</tbody></table>"
        + "<h2>Participation</h2>" + _roll_table(payload.get("roll", []))
        + "<h2>Ballots</h2>" + _ballot_table(payload.get("ballots", []))
    )
    return document(
        f"{proposition_facts.get('title', proposition.title)} — decision",
        body, css=VOTE_CSS,
    )


def meeting_document(meeting) -> str:
    """The meeting record: attendance, quorum, each proposition and its result."""
    from toto.voting.models import ProposalStatus
    from toto.voting.services.lifecycle import result_document

    sections = []
    for proposition in meeting.propositions.order_by("order"):
        if proposition.status == ProposalStatus.CLOSED and proposition.result_payload:
            payload = proposition.result_payload
        else:
            payload = result_document(proposition)
        result = payload.get("result", {})
        sections.append(
            f"<h2>{proposition.order}. {escape(proposition.title)}</h2>"
            f"<p class='resolution'>{escape(proposition.resolution_text)}</p>"
            f"<p class='outcome'>Outcome: {escape(payload.get('outcome', ''))}</p>"
            + _result_table(result)
            + "<h3 style='font-size:10pt;margin:4mm 0 1mm'>Ballots</h3>"
            + _ballot_table(payload.get("ballots", []))
        )

    roll = [{
        "voter": row.voter_name,
        "source": row.get_source_display(),
        "status": row.get_status_display(),
        "represented_by": row.represented_by,
        "weight": str(row.weight),
    } for row in meeting.roll.order_by("voter_name")]

    body = (
        f"<h1>{escape(meeting.title)}</h1>"
        f"<p class='meta'>{escape(meeting.get_status_display())} · "
        f"record date {meeting.record_date} · "
        f"{'held ' + meeting.held_at.strftime('%Y-%m-%d %H:%M') if meeting.held_at else 'not yet held'}</p>"
        "<h2>Attendance and quorum</h2>"
        "<table><tbody>"
        f"<tr><th style='width:38%'>Eligible weight</th><td>{meeting.eligible_weight}</td></tr>"
        f"<tr><th>Present weight</th><td>{meeting.present_weight}</td></tr>"
        f"<tr><th>Voters present</th><td>{meeting.present_voters} of {meeting.eligible_voters}</td></tr>"
        f"<tr><th>Quorum</th><td>{'met' if meeting.quorum_met else 'not met'}</td></tr>"
        "</tbody></table>"
        + _roll_table(roll)
        + "".join(sections)
    )
    return document(f"{meeting.title} — meeting record", body, css=VOTE_CSS)


def register_table(rows) -> str:
    """The shareholder register, both metrics named — never one called
    "share": ownership is units over total units, voting power is votes over
    total votes, and they diverge whenever a class carries other than one
    vote per unit."""
    cells = "".join(
        f"<tr><td>{escape(row['name'])}</td>"
        f"<td>{escape(row['holding'].share_class.name)}</td>"
        f"<td class=\"num\">{row['units']}</td>"
        f"<td class=\"num\">{row['ownership_percent']}%</td>"
        f"<td class=\"num\">{row['votes']}</td>"
        f"<td class=\"num\">{row['voting_percent']}%</td></tr>"
        for row in rows
    )
    return (
        "<table><thead><tr><th>Shareholder</th><th>Share class</th>"
        "<th>Units</th><th>Ownership %</th><th>Votes</th><th>Voting %</th>"
        "</tr></thead><tbody>" + cells + "</tbody></table>"
    )


def register_document(company, register) -> str:
    """The one-click shareholder-register PDF's HTML.

    ``register`` is ``toto.company.views.ownership_register(company)`` —
    built there, rendered here, so the numbers on the PDF are the numbers on
    the page, from the same code path.
    """
    totals = (
        f"<p class=\"totals\">Total: {register['total_units']} units, "
        f"{register['total_votes']} votes.</p>"
    )
    body = (
        f"<h2>{escape(company.name)}</h2>"
        f"<p class=\"meta\">Shareholder register — exact to the last unit. "
        f"Units are capital; votes are control.</p>"
        + register_table(register["shareholder_structure"])
        + totals
    )
    return document(f"Shareholder register — {company.name}", body)
