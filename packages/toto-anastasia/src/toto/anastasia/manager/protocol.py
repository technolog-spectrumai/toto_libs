"""How a caller proves it is allowed to ask, over an internal network.

HMAC-SHA256 over a canonical string, with a timestamp window and a nonce cache.
Not TLS-and-a-bearer-token, for three reasons worth stating once:

* the manager listens on an internal docker network and a unix-domain or
  tailnet path, so transport secrecy is already provided by the deployment;
  what is missing is *authenticity*, which is what this adds;
* a bearer token is replayable by anything that ever sees one request; a
  signature over the body is not;
* the secret is a stack ``.env`` value shared by exactly two processes, so
  there is no key distribution problem to solve badly.

The canonical string binds METHOD, PATH, a hash of the BODY, the timestamp and
the nonce. Binding the body is the point: without it a valid signature for
``POST /executions`` could be replayed with a different payload.

Django-free — the manager imports this with no settings module.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time

#: How far apart the two clocks may be. Generous enough for a container that
#: has just started and has not yet been NTP-corrected, tight enough that a
#: captured request is useless by the time anyone replays it by hand.
CLOCK_SKEW_SECONDS = 300

#: Header names. Lowercase because that is how every HTTP library normalises
#: them and this is not a place to discover a case bug.
HEADER_SIGNATURE = "x-anastasia-signature"
HEADER_TIMESTAMP = "x-anastasia-timestamp"
HEADER_NONCE = "x-anastasia-nonce"

MAX_NONCE_LENGTH = 64


class SignatureError(Exception):
    """A request that could not be authenticated. The message is for a LOG.

    Never for a caller: a verification failure must not explain which half was
    wrong, because that turns the endpoint into an oracle. The service answers
    401 with a fixed sentence and writes this to the log.
    """


def body_digest(body: bytes) -> str:
    return hashlib.sha256(body or b"").hexdigest()


def canonical_string(*, method: str, path: str, body: bytes, timestamp: str,
                     nonce: str) -> str:
    """The exact bytes both sides sign.

    Newline-joined with no length prefixes is safe here only because none of
    the fields may contain a newline: method and nonce are constrained
    character sets, the digest is hex, the timestamp is digits, and the path is
    taken from the request line which cannot contain one. A field that could
    would need framing.
    """
    return "\n".join([
        method.upper(),
        path,
        body_digest(body),
        str(timestamp),
        nonce,
    ])


def sign(*, secret: str, method: str, path: str, body: bytes,
         timestamp: str | None = None, nonce: str | None = None) -> dict:
    """Produce the three headers a request needs."""
    if not secret:
        raise SignatureError("no shared secret is configured")
    timestamp = str(timestamp if timestamp is not None else int(time.time()))
    nonce = nonce or secrets.token_urlsafe(16)
    message = canonical_string(method=method, path=path, body=body,
                               timestamp=timestamp, nonce=nonce)
    digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"),
                      hashlib.sha256).hexdigest()
    return {
        HEADER_SIGNATURE: digest,
        HEADER_TIMESTAMP: timestamp,
        HEADER_NONCE: nonce,
    }


class NonceCache:
    """Remembers recently-seen nonces so a captured request cannot be replayed.

    Bounded by the clock-skew window rather than by count: anything older than
    the window is already refused by the timestamp check, so it need not be
    remembered. That makes the cache self-limiting without an eviction policy
    to get wrong — and it is why the window is small.
    """

    def __init__(self, window_seconds: int = CLOCK_SKEW_SECONDS):
        self.window = window_seconds
        self._seen: dict[str, float] = {}

    def _prune(self, now: float) -> None:
        cutoff = now - self.window
        stale = [key for key, seen in self._seen.items() if seen < cutoff]
        for key in stale:
            del self._seen[key]

    def check_and_add(self, nonce: str, now: float | None = None) -> bool:
        """True if this nonce is fresh. False means: already used, refuse."""
        now = now if now is not None else time.time()
        self._prune(now)
        if nonce in self._seen:
            return False
        self._seen[nonce] = now
        return True


def verify(*, secret: str, method: str, path: str, body: bytes, headers,
           nonces: NonceCache | None = None, now: float | None = None) -> None:
    """Raise :class:`SignatureError` unless this request is authentic and fresh."""
    if not secret:
        raise SignatureError("no shared secret is configured")

    def header(name: str) -> str:
        # Accept either a plain dict or an http.client-style message object.
        getter = getattr(headers, "get", None)
        value = getter(name) if getter else None
        if value is None and getter:
            value = getter(name.title())
        return (value or "").strip()

    signature = header(HEADER_SIGNATURE)
    timestamp = header(HEADER_TIMESTAMP)
    nonce = header(HEADER_NONCE)

    if not signature or not timestamp or not nonce:
        raise SignatureError("request is missing its signature headers")
    if len(nonce) > MAX_NONCE_LENGTH:
        raise SignatureError("nonce is implausibly long")

    try:
        sent_at = float(timestamp)
    except ValueError:
        raise SignatureError("timestamp is not a number") from None

    now = now if now is not None else time.time()
    if abs(now - sent_at) > CLOCK_SKEW_SECONDS:
        raise SignatureError(
            f"timestamp is {abs(now - sent_at):.0f}s away from now")

    expected = hmac.new(
        secret.encode("utf-8"),
        canonical_string(method=method, path=path, body=body,
                         timestamp=timestamp, nonce=nonce).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    # compare_digest, never ==: a byte-at-a-time comparison leaks the prefix
    # length through timing, and this endpoint is reachable by anything on the
    # internal network.
    if not hmac.compare_digest(expected, signature):
        raise SignatureError("signature does not match")

    if nonces is not None and not nonces.check_and_add(nonce, now):
        raise SignatureError("nonce has already been used")


def encode(payload) -> bytes:
    """Canonical JSON: sorted keys, no incidental whitespace.

    Deterministic because the body is signed — a dict that serialises two ways
    would produce two signatures for one request.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      default=str).encode("utf-8")


def decode(body: bytes):
    try:
        return json.loads((body or b"{}").decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"request body is not valid JSON: {exc}") from None
