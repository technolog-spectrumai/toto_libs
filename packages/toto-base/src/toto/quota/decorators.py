"""View decorators that turn a quota or funding refusal into an HTTP answer.

Stacked below the auth decorator and any method guard, closest to the view —
the same shape as gitvault's ``_json_errors``::

    @login_required
    @require_POST
    @quota_limited(TexlabQuotaPolicy, "texlab.compile")
    def compile_latex(request, file_pk):
        ...

Two refusals, two codes, because they are different problems: **429** when the
user is over their rate limit, **402** when they simply cannot pay for it.
"""

from __future__ import annotations

import functools

from django.http import JsonResponse
from django.shortcuts import render

from .api import QuotaExceeded, check_quota, record_usage
from .charge import InsufficientFunds


def _wants_json(request) -> bool:
    """Whether to answer with a body or a page.

    Guessing is unavoidable — this codebase has HTML views, JSON views, and at
    least one view that is both depending on the request (manta's command
    builder). The signals below are the ones those views already use.
    """
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return True
    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        return True
    return request.content_type == "application/json"


def _refuse(request, exc, response: str):
    as_json = _wants_json(request) if response == "auto" else (response == "json")
    status = getattr(exc, "status_code", 429)

    if as_json:
        body = {"error": str(exc)}
        if status == 402:
            body["payment_required"] = True
            body["shortfall"] = str(getattr(exc, "shortfall_display", "") or "")
            body["asset"] = getattr(exc, "asset_name", "") or ""
        else:
            body["quota_exceeded"] = True
        return JsonResponse(body, status=status)

    template = "quota/payment_required.html" if status == 402 else "quota/limit_reached.html"
    return render(request, template, {"reason": str(exc), "error": exc}, status=status)


def quota_limited(policy_model, metric_code: str, *, quantity=1,
                  response: str = "auto", record: bool = True):
    """Refuse the request when the user is over the limit for this metric.

    Usage is recorded after the view returns a 2xx, so a rejected or failed
    request does not count against the limit.
    """
    def decorator(view):
        @functools.wraps(view)
        def wrapped(request, *args, **kwargs):
            user = getattr(request, "user", None)
            user = user if getattr(user, "is_authenticated", False) else None

            try:
                check_quota(policy_model, metric_code, quantity, user)
            except QuotaExceeded as exc:
                return _refuse(request, exc, response)

            result = view(request, *args, **kwargs)

            if record and 200 <= getattr(result, "status_code", 500) < 300:
                events = getattr(policy_model, "events", None)
                if events is not None:
                    record_usage(events, metric_code, quantity, user)
            return result

        return wrapped

    return decorator


def funds_required(app_label: str, metric_code: str, *, quantity=1, response: str = "auto"):
    """Refuse the request when the user cannot pay for this action.

    A no-op on a host without billing, and on a metric with no price — see
    :mod:`toto.quota.charge` for why those are the same thing.
    """
    from .charge import check_funds, price_for

    def decorator(view):
        @functools.wraps(view)
        def wrapped(request, *args, **kwargs):
            user = getattr(request, "user", None)
            if getattr(user, "is_authenticated", False):
                tariff = price_for(user, app_label)
                try:
                    check_funds(user, tariff, metric_code, quantity)
                except InsufficientFunds as exc:
                    return _refuse(request, exc, response)
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
