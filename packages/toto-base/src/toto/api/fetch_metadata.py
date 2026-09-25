"""Refuse a cookie-authenticated write that a browser sent from another site.

Several JSON doors are `csrf_exempt` because desktop clients post to them
with a Bearer token (zenobia/todo.md, item 2). A browser attaches the
member's cookies to a same-site form or fetch just as happily, so those doors
accept a forged write. Every modern browser labels its requests with
``Sec-Fetch-Site``; a write labelled ``same-site`` or ``cross-site`` that was
authenticated by cookie is refused. A request without the header (a
non-browser client) or authenticated by a Bearer token passes.
"""

from __future__ import annotations

from django.http import JsonResponse

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
FOREIGN = ("same-site", "cross-site")


def cross_site_refusal(request):
    """A 403 JsonResponse for a forged cookie write, else None."""
    if request.method in SAFE_METHODS:
        return None
    if getattr(request, "_toto_bearer_auth", False):
        return None
    site = (request.META.get("HTTP_SEC_FETCH_SITE") or "").lower()
    if site in FOREIGN:
        return JsonResponse({"error": "Cross-site request refused."}, status=403)
    return None
