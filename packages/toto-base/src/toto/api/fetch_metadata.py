"""Refuse a cookie-authenticated write that a browser sent from another site.

Several JSON doors are `csrf_exempt` because desktop clients post to them
with a Bearer token (zenobia/todo.md, item 2). A browser attaches the
member's cookies to a same-site form or fetch just as happily, so those doors
accept a forged write. Every modern browser labels its requests with
``Sec-Fetch-Site``; a write labelled ``same-site`` or ``cross-site`` that was
authenticated by cookie is refused. A request without the header (a
non-browser client) or authenticated by a Bearer token passes.

Since stage 51 every ``CorsApiView`` asks this first (``toto.api.cors``), and
two more rules hold: a browser too old to send the label still sends
``Origin`` on a POST, so an Origin naming another host is refused the same
way; and a request from an origin the host names exactly in
``CORS_ALLOWED_ORIGINS`` (the desktop app's webview, which a browser labels
cross-site) passes, as CORS already trusts that origin with credentials.

A READ is refused the same way where a door asks for it (``reads=True``,
2026-10-04): the long-poll door ``notify:api_wait`` holds a request open,
and a page of another site must not be able to make a member's browser hold
one, nor time its answer.
"""

from __future__ import annotations

from urllib.parse import urlparse

from django.http import JsonResponse

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
FOREIGN = ("same-site", "cross-site")


def _foreign_origin(request, origin: str) -> bool:
    """Whether an Origin header names a host other than the one asked."""
    if origin.strip().lower() == "null":
        return True                       # a sandboxed or opaque origin
    try:
        theirs = (urlparse(origin).hostname or "").lower()
        ours = request.get_host().rsplit(":", 1)[0].strip("[]").lower()
    except Exception:
        return True
    return not theirs or theirs != ours


def cross_site_refusal(request, *, reads: bool = False):
    """A 403 JsonResponse for a forged cookie write, else None. With
    ``reads`` a GET is held to the same rule."""
    if request.method in SAFE_METHODS and not reads:
        return None
    if getattr(request, "_toto_bearer_auth", False):
        return None
    origin = request.META.get("HTTP_ORIGIN") or ""
    if origin:
        from .cors import _is_allowed_origin

        if _is_allowed_origin(origin):
            return None
    site = (request.META.get("HTTP_SEC_FETCH_SITE") or "").lower()
    if site in FOREIGN or (not site and origin and _foreign_origin(request, origin)):
        return JsonResponse({"error": "Cross-site request refused."}, status=403)
    return None
