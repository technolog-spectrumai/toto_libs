"""Handing a generated PDF to a browser, metered the platform's way.

The shape portfolio/views.py established and every export copies: quota and
funds checked BEFORE the work, charged AFTER bytes exist, and every refusal
answered as plain text with its own status — a download target that responds
with a redirect and a flash message is one whose failure the browser swallows.

Lives here rather than inside views.py because Business Center serves the same
exports for one company, and a second copy of a billing sequence is how one
copy quietly stops charging.
"""

from __future__ import annotations

from django.http import HttpResponse

from . import render_pdf
from .models import PollsQuotaPolicy, PollsUsageEvent


def metered_pdf(request, build, filename: str) -> HttpResponse:
    """Run ``build()`` under the polls.pdf meter and return it as a download.

    ``build`` is a zero-argument callable returning bytes; it may raise
    :class:`~toto.polls.render_pdf.PdfUnavailable`, which becomes a 503 with
    the sentence explaining that this deployment has no renderer.
    """
    from toto.quota import QuotaExceeded, check_quota, record_usage
    from toto.quota.charge import (InsufficientFunds, charge, check_funds,
                                   price_for)

    tariff = price_for(request.user, "polls")
    try:
        check_quota(PollsQuotaPolicy, "polls.pdf", 1, request.user)
        check_funds(request.user, tariff, "polls.pdf", 1)
    except (QuotaExceeded, InsufficientFunds) as exc:
        # Plain text, not messages+redirect: this is a download target.
        return HttpResponse(str(exc), status=exc.status_code,
                            content_type="text/plain")

    try:
        raw = build()
    except render_pdf.PdfUnavailable as exc:
        # A deployment fact, not something the user can fix by retrying.
        return HttpResponse(str(exc), status=503, content_type="text/plain")

    # Charged after the render succeeds — nothing to refund on a synchronous
    # call that either returns bytes or raised before this line.
    record_usage(PollsUsageEvent, "polls.pdf", 1, request.user)
    charge(request.user, tariff, "polls.pdf", 1)

    response = HttpResponse(raw, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
