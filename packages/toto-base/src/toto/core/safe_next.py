"""Where a request may send its member next, said in one place (2026-09-30).

A ``?next=``, a posted ``next`` or ``back``, a ``Referer`` — each is written
by whoever built the link, so following one unchecked is an open redirect: a
phishing mail links to this site's real sign-in page with
``?next=https://evil.example.com/``, the member signs in on the genuine page
and lands on the copy. The sign-in and sign-out doors did exactly that until
now (stage 31.11).

``safe_next`` keeps the candidate only when Django's own
``url_has_allowed_host_and_scheme`` accepts it for this request's host — a
path on this site, or an absolute URL naming this host — and, when the
request came over HTTPS, only an HTTPS one. Anything else (another host, a
``//host`` scheme-relative URL, ``javascript:``, a blank) answers the
fallback, which is each door's normal landing page.

    safe_next(request, "/vault/")                      -> "/vault/"
    safe_next(request, "https://evil.example.com/", "/") -> "/"
"""

from __future__ import annotations

from django.utils.http import url_has_allowed_host_and_scheme


def safe_next(request, candidate, fallback=""):
    candidate = (candidate or "").strip()
    if candidate and url_has_allowed_host_and_scheme(
            candidate, allowed_hosts={request.get_host()},
            require_https=request.is_secure()):
        return candidate
    return fallback
