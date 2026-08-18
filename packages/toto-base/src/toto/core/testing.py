"""Helpers for suites that run on hosts with different front doors.

## Why this exists

A wheel's tests run on every host that pins it, and the hosts do not agree about
how an anonymous caller is turned away. Most hosts let each view answer for
itself — usually ``403``, sometimes a ``302`` to a login page. zenobia installs
``LoginRequiredEverywhereMiddleware``, which refuses first, so the view never
runs and its own answer is never seen.

Asserting one exact status therefore encodes a HOST's policy into a WHEEL's
test, and thirteen suites duly went red on zenobia while the property they
existed to check — an anonymous caller cannot get in — held perfectly.

The fix is to assert the property rather than the mechanism. That keeps the
coverage (a view that started answering ``200`` still fails loudly) without
pinning which layer does the refusing.
"""

from __future__ import annotations

#: Every status that means "you are not getting in without signing in first".
#:
#: * ``302`` — a redirect to the login page. What a browser wants, and what a
#:   host-wide gate does before any view runs.
#: * ``401`` — for a caller that asked for JSON, or reached an ``/api/`` path.
#:   A redirect there reads as success to most clients.
#: * ``403`` — a view refusing on its own, typically because the caller is
#:   authenticated but lacks a role. Included because a view that gates itself
#:   answers this to anonymous callers too when nothing refused them earlier.
ANONYMOUS_REFUSED = (302, 401, 403)


def assert_refused(test, response, what: str = "") -> None:
    """Assert an anonymous caller was turned away, however this host does it."""
    test.assertIn(
        response.status_code, ANONYMOUS_REFUSED,
        f"{what or 'this endpoint'} answered {response.status_code} to an "
        f"anonymous caller; expected one of {ANONYMOUS_REFUSED} — a refusal, "
        f"by whichever layer refuses on this host.")
