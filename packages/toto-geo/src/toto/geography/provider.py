"""Reading an outside service's answer: with a cap and under a deadline.

The geocoder (``LOCATIONS_GEOCODING``) and the router (``GEOGRAPHY_ROUTING``)
are outside services. ``urlopen``'s ``timeout`` bounds each single wait on
the socket, not the exchange, and a bare ``read()`` takes whatever comes: a
service that sends a little every few seconds, or far too much, would hold a
web worker and its memory for as long as it liked. So both are asked through
``fetch_json``:

    the cap       at most ``limit`` bytes are read; one more is a ValueError
    the deadline  ``timeout`` seconds from before the request is sent; a body
                  that has not ended by then is a TimeoutError

Both are in the providers' error sets (``PROVIDER_ERRORS``: OSError,
HTTPException, ValueError), so the door answers 503 and charges nothing.

WHAT THE DEADLINE IS AND IS NOT. The body is read one socket read at a time
(``read1``) and the clock is looked at before each, so once the answer's
headers are in, the exchange ends within the deadline plus at most one more
wait on the socket (itself at most ``timeout``). Before that, connecting and
receiving the headers are bounded per wait by ``urlopen``'s own timeout and
not as a whole; a name that does not resolve is the resolver's to give up
on. A worker is therefore held for about twice ``timeout`` at the worst a
slow but honest service gives, not for ever.

Nothing here knows what is asked: no query, no coordinate, no member.
"""

from __future__ import annotations

import json
import time
from urllib.request import urlopen

#: How much is asked of the socket at a time.
CHUNK = 64 * 1024


def read_capped(response, limit: int, deadline: float) -> bytes:
    """The body of ``response``: at most ``limit`` bytes, ended by
    ``deadline`` (a ``time.monotonic()`` value)."""
    # One socket read a turn where the response can do that: ``read(n)``
    # waits until it has all n bytes, however slowly they come.
    step = getattr(response, "read1", None) or response.read
    parts, size = [], 0
    while True:
        if time.monotonic() > deadline:
            raise TimeoutError("the answer did not end in time")
        chunk = step(CHUNK)
        if not chunk:
            return b"".join(parts)
        size += len(chunk)
        if size > limit:
            raise ValueError("the answer is larger than this server reads")
        parts.append(chunk)


def fetch_json(request, *, timeout, limit: int):
    """Send ``request`` and answer its body, parsed as JSON. Raises what
    ``urlopen`` raises, TimeoutError past the deadline, and ValueError for a
    body past ``limit`` or one that is no JSON in UTF-8."""
    deadline = time.monotonic() + timeout
    with urlopen(request, timeout=timeout) as response:
        body = read_capped(response, limit, deadline)
    return parse(body)


def parse(body: bytes):
    """``body`` as JSON. A body nested past the parser's depth is a
    RecursionError there, which is no ValueError: it is made one."""
    try:
        return json.loads(body.decode("utf-8"))
    except RecursionError:
        raise ValueError("the answer is nested deeper than this server reads") from None
