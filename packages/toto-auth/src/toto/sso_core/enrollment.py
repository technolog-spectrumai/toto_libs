"""The federation pairing wire format — what the QR carries, and what the two
servers say to each other afterwards.

Plain dataclasses and bytes, no Django, so both sides of a federation can import
it and neither app has to import the other. Sits beside ``manifest.py``, which it
replaces: that carried the client secret through a downloaded file and a human's
clipboard, and this exists so it never has to again.

## The ticket

    base32_nopad_lowercase( TAG(1) ‖ VERSION(1) ‖ SECRET(32) ‖ URL(utf-8) )

A **fixed-width prefix then the rest is the URL** — no separator, no length
field, no serializer. Smaller than JSON, and it needs no msgpack, which is not a
dependency here.

**Nothing else goes in the ticket.** Not the expected host, the granted scopes,
the trusted flag or the expiry — those are columns on the invite row. Putting
them on the wire would let a redeemer edit ``scopes`` to include ``roles`` (which
propagates staff and superuser to the child) or set ``trusted`` (which skips the
consent screen) before redeeming. The grant is fixed at mint time and read from
the database, never from the message.

The leading **tag byte** is enigma's idea and worth keeping
(``device_link.rs:47``, ``p2p_iroh.rs:81``): one paste field can route several
kinds of credential, and pasting the wrong kind fails immediately with a sentence
rather than a stack trace.

For a 34-character provider URL the whole thing is 68 bytes → **109 base32
characters**, which renders as a 57×57 QR — comfortably photographable.

## What is secret

``SECRET`` is 32 random bytes and it is a **bearer credential**: whoever holds the
ticket can redeem it. The provider stores only ``sha256`` of it, so a database
leak does not yield a redeemable ticket, and it is single-use with a short TTL.

Three things bound it, and they are the whole security argument:

1. **Single use**, taken under ``select_for_update`` so two simultaneous
   redemptions cannot both win.
2. **A short expiry** — five minutes, ample for a paste, short enough that a code
   scraped from a proxy log is dead before anyone reads the log.
3. **The expected-host pin**, checked at redemption. This is what stands in for
   the human confirmation step enigma relies on (``device_link.rs:16-17``: "the
   human backstop against a photographed QR"), which a headless server does not
   have. Without it a stolen ticket lets the thief register *their* callback as a
   trusted relying party and receive tokens for the provider's users; with it the
   ticket is inert unless they also control the host the admin already named.

Plus ``deployment_mac`` below, which binds the row to the deployment that minted
it — see its docstring for why ``.env`` and the database having different
lifetimes makes that necessary.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass

# 'F' for federation. Distinct from any other credential this suite might paste
# into the same box later.
TICKET_TAG = 0x46
TICKET_VERSION = 1
SECRET_BYTES = 32

# Domain separation, so a MAC over one thing can never be replayed as a MAC over
# another. Each context is used with a different key and a different input shape.
INVITE_MAC_CONTEXT = b"toto/federation/v1/invite"
SIDECAR_CONTEXT = b"toto/federation/v1/sidecar"


class TicketError(ValueError):
    """A pairing ticket is malformed, truncated, or of the wrong kind."""


@dataclass(frozen=True)
class Ticket:
    """What the QR carries: where to go, and the secret that proves you were invited."""

    version: int
    url: str
    secret: bytes

    @property
    def secret_sha256(self) -> str:
        return hashlib.sha256(self.secret).hexdigest()

    @property
    def prefix(self) -> str:
        """The first 8 characters of the encoded form — safe to show in a list.

        Enough to tell two invites apart in the admin, far too little to redeem.
        """
        return encode_ticket(self.url, self.secret)[:8]


def new_secret() -> bytes:
    return secrets.token_bytes(SECRET_BYTES)


def encode_ticket(url: str, secret: bytes) -> str:
    """The string that goes in the QR and in the copy field beside it."""
    if len(secret) != SECRET_BYTES:
        raise TicketError(f"A pairing secret must be {SECRET_BYTES} bytes.")
    url_bytes = url.encode("utf-8")
    if not url_bytes:
        raise TicketError("A pairing ticket needs a provider URL.")
    raw = bytes([TICKET_TAG, TICKET_VERSION]) + secret + url_bytes
    return base64.b32encode(raw).decode("ascii").rstrip("=").lower()


def decode_ticket(text: str) -> Ticket:
    """Parse a pasted or scanned ticket. Every failure names what is wrong."""
    cleaned = "".join((text or "").split()).lower()
    if not cleaned:
        raise TicketError("No pairing code was given.")

    # b32decode wants the padding back and rejects anything outside the alphabet.
    padding = "=" * (-len(cleaned) % 8)
    try:
        raw = base64.b32decode(cleaned.upper() + padding)
    except Exception as exc:  # noqa: BLE001 — binascii raises several types
        raise TicketError(
            "That does not look like a pairing code — it should be letters a-z "
            "and digits 2-7 only."
        ) from exc

    if len(raw) < 2 + SECRET_BYTES + 1:
        raise TicketError("That pairing code is truncated.")
    if raw[0] != TICKET_TAG:
        raise TicketError(
            "That is not a federation pairing code (it may be a code for "
            "something else)."
        )
    version = raw[1]
    if version != TICKET_VERSION:
        raise TicketError(
            f"That pairing code is version {version}; this platform speaks "
            f"version {TICKET_VERSION}. Upgrade the other side, or mint a new code here."
        )

    secret = raw[2:2 + SECRET_BYTES]
    try:
        url = raw[2 + SECRET_BYTES:].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TicketError("That pairing code carries an unreadable provider URL.") from exc
    return Ticket(version=version, url=url, secret=secret)


def deployment_mac(federation_key: str, handle: str) -> str:
    """Bind an invite to the deployment that minted it.

    ``.env`` and the database have **different lifetimes**, and that is the whole
    reason this exists. ``RESET=1`` wipes the database while ``.env`` survives, and
    a production backup restored onto a staging box arrives with production's
    pending invite rows but staging's key. Without this the restored rows would be
    redeemable on the wrong server; with it they are inert.

    Blast radius is deliberately small and knowable: rotating or losing
    ``FEDERATION_KEY`` voids pending invites and nothing else. That is exactly why
    the key is not allowed to own a client secret — see ``sidecar_secret``.
    """
    if not federation_key:
        return ""
    message = INVITE_MAC_CONTEXT + b"\0" + _lp(handle)
    return hmac.new(
        federation_key.encode("utf-8"), message, hashlib.sha256,
    ).hexdigest()


def sidecar_secret(federation_key: str, client_id: str) -> str:
    """A first client secret for a sidecar that has none yet.

    Used **only** as the last fallback in ``deploy.py``, behind an explicit config
    value and behind whatever is already in ``.env``. It therefore mints a secret
    for a fresh deployment and never replaces a live one — which is the whole
    safety argument, since replacing Gitea's secret out from under its one-shot,
    no-retry provisioning container is how that federation breaks permanently.
    """
    message = SIDECAR_CONTEXT + b"\0" + _lp(client_id)
    digest = hmac.new(federation_key.encode("utf-8"), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def macs_match(expected: str, given: str) -> bool:
    """Constant-time compare. Never use ``==`` on a MAC."""
    return hmac.compare_digest(expected or "", given or "")


def _lp(value: str) -> bytes:
    """Length-prefixed, so no two different inputs can produce the same message.

    enigma's equivalent concatenates with a ``|`` separator
    (``device_link.rs:144-149``), which is ambiguous the moment a field can contain
    one. Four bytes of length costs nothing and removes the whole class.
    """
    raw = (value or "").encode("utf-8")
    return len(raw).to_bytes(4, "big") + raw


# -- the two wire messages ------------------------------------------------


@dataclass(frozen=True)
class EnrollmentRequest:
    """Consumer -> provider, at ``POST /sso/enroll/``.

    **There is deliberately no proof or nonce here.** An earlier draft sent the
    ticket alongside ``HMAC(secret, ...)`` keyed by the very secret the ticket
    decodes to — anyone who could read the request held the MAC key, so it
    defended against nobody. Replay is prevented by single-use redemption under a
    row lock, not by a nonce.

    The alternative that looks appealing — send ``sha256(secret)`` plus a MAC so
    the secret never travels — cannot work: the provider stores only the hash, so
    it has nothing to verify a MAC with. Making it work means storing redeemable
    secrets in plaintext, trading a request-log exposure for a database one. For a
    five-minute single-use code that is a bad trade.

    So this is the ordinary bearer-token shape, the same one Django's own password
    reset uses: the code travels over TLS, is stored only as a hash, is single use,
    and expires quickly.
    """

    ticket: str
    callback_uri: str
    label: str

    def to_dict(self) -> dict:
        return {
            "ticket": self.ticket,
            "callback_uri": self.callback_uri,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EnrollmentRequest":
        missing = [
            k for k in ("ticket", "callback_uri")
            if not str(data.get(k) or "").strip()
        ]
        if missing:
            raise TicketError(f"Enrollment request is missing: {', '.join(missing)}")
        return cls(
            ticket=str(data["ticket"]).strip(),
            callback_uri=str(data["callback_uri"]).strip(),
            label=str(data.get("label") or "").strip()[:100],
        )


@dataclass(frozen=True)
class EnrollmentGrant:
    """Provider → consumer. Everything the consumer needs, discovered not guessed.

    The endpoints travel explicitly because the consumer currently builds them by
    string concatenation (``sso_client/views.py:286``, ``:315`` do
    ``f"{portal}/sso/token/"``), which silently breaks against any provider
    mounted at a different prefix. Learning them at pairing time costs one field
    each and removes a whole class of misconfiguration.
    """

    client_id: str
    client_secret: str
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    userinfo_endpoint: str
    jwks_uri: str
    scopes: str
    relying_party_uuid: str
    label: str

    def to_dict(self) -> dict:
        return {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "issuer": self.issuer,
            "authorization_endpoint": self.authorization_endpoint,
            "token_endpoint": self.token_endpoint,
            "userinfo_endpoint": self.userinfo_endpoint,
            "jwks_uri": self.jwks_uri,
            "scopes": self.scopes,
            "relying_party_uuid": self.relying_party_uuid,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EnrollmentGrant":
        required = (
            "client_id", "client_secret", "issuer", "authorization_endpoint",
            "token_endpoint", "userinfo_endpoint", "jwks_uri",
        )
        missing = [k for k in required if not str(data.get(k) or "").strip()]
        if missing:
            raise TicketError(
                "The provider's reply is missing: " + ", ".join(missing)
            )
        return cls(
            client_id=str(data["client_id"]),
            client_secret=str(data["client_secret"]),
            issuer=str(data["issuer"]),
            authorization_endpoint=str(data["authorization_endpoint"]),
            token_endpoint=str(data["token_endpoint"]),
            userinfo_endpoint=str(data["userinfo_endpoint"]),
            jwks_uri=str(data["jwks_uri"]),
            scopes=str(data.get("scopes") or "openid email profile"),
            relying_party_uuid=str(data.get("relying_party_uuid") or ""),
            label=str(data.get("label") or ""),
        )
