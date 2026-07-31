"""Fetching inbound mail over IMAP — the read counterpart to ``delivery.py``.

On demand only: a human triggers a fetch (``views.inbox_fetch``), so there is no
background poller that would need a server-side passphrase it cannot have in
manual-release mode. The password is decrypted through the vault exactly as the SMTP one
is at send time — the ambient session by default, an admin-typed one under manual release.

Everything here is testable without a live server: ``parse_message`` is a pure function,
and ``fetch_new`` takes its message bytes from a connection a test can fake.
"""
from __future__ import annotations

import imaplib
import uuid
from email import message_from_bytes
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime

from . import vault
from .models import InboundMessage

# The most messages one on-demand fetch will pull. On-demand means a human is waiting on
# the request, so this bounds it the way RELEASE_BATCH_CAP bounds a manual release.
FETCH_CAP = 50


class NoMailbox(RuntimeError):
    """The provider is not configured to receive mail."""


def build_imap_connection(provider, *, session=None):
    """Open and log into the provider's IMAP mailbox. Raises on any failure.

    ``session`` injects an admin-typed vault session (manual release); without it the
    ambient env session is used, which raises ``VaultUnavailable`` in manual mode — the
    same contract as ``delivery.build_connection``.
    """
    if not (provider.imap_host or "").strip():
        raise NoMailbox(
            f"'{provider.label}' has no IMAP host, so there is no mailbox to read. "
            "Add one on the email-setup page."
        )
    password = ""
    if provider.secret_id:
        password = vault.read_secret(provider.secret, session=session)
    cls = imaplib.IMAP4_SSL if provider.imap_use_ssl else imaplib.IMAP4
    conn = cls(provider.imap_host, provider.imap_port)
    conn.login(provider.username or "", password)
    return conn


def _decode(value) -> str:
    """A header value as a plain string, MIME encoded-words decoded. Never raises."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:                           # noqa: BLE001 — a bad header must not sink a fetch
        return str(value)


def _text(part) -> str:
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:                         # an unknown charset name
        return payload.decode("utf-8", errors="replace")


def _body_parts(msg) -> tuple[str, str]:
    """(plain, html) bodies, ignoring attachments."""
    plain = html = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() != "text":
                continue
            if (part.get("Content-Disposition") or "").strip().lower().startswith("attachment"):
                continue
            sub = part.get_content_subtype()
            if sub == "html" and not html:
                html = _text(part)
            elif sub == "plain" and not plain:
                plain = _text(part)
    elif msg.get_content_subtype() == "html":
        html = _text(msg)
    else:
        plain = _text(msg)
    return plain, html


def parse_message(raw: bytes) -> dict:
    """Turn raw RFC822 bytes into the fields ``InboundMessage`` stores. Pure and testable."""
    msg = message_from_bytes(raw)
    plain, html = _body_parts(msg)

    froms = getaddresses(msg.get_all("From", []))
    from_address = froms[0][1] if froms else ""
    to = [addr for _name, addr in getaddresses(msg.get_all("To", [])) if addr]

    date_header = None
    if msg.get("Date"):
        try:
            date_header = parsedate_to_datetime(msg.get("Date"))
        except (TypeError, ValueError):
            date_header = None

    return {
        "from_address": from_address[:255],
        "to": to,
        "subject": _decode(msg.get("Subject", "")),
        "body": plain,
        "html_body": html,
        "message_id": (msg.get("Message-ID", "") or "").strip(),
        "in_reply_to": (msg.get("In-Reply-To", "") or "").strip(),
        "references": (msg.get("References", "") or "").split(),
        "date_header": date_header,
        "headers": {k: _decode(v) for k, v in msg.items()},
    }


def store_message(raw: bytes, *, provider):
    """Persist one message, deduped by Message-ID. Returns the row, or None if a dupe.

    A (rare, non-conformant) message with no Message-ID gets a synthesised one so the
    unique column is satisfied and it is not silently dropped.
    """
    fields = parse_message(raw)
    mid = fields["message_id"] or f"<no-id-{uuid.uuid4().hex}@jess.local>"
    fields["message_id"] = mid
    if InboundMessage.objects.filter(message_id=mid).exists():
        return None
    return InboundMessage.objects.create(provider=provider, **fields)


def fetch_new(provider, *, session=None, limit: int = FETCH_CAP) -> int:
    """Fetch the newest messages from the provider's mailbox and store the new ones.

    Returns how many NEW rows were written. Idempotent: anything already stored (by
    Message-ID) is skipped, so fetching twice is safe. Never auto-retries — a failure
    raises and the caller records it, the house rule everywhere in Jess.
    """
    conn = build_imap_connection(provider, session=session)
    stored = 0
    try:
        conn.select(provider.mailbox or "INBOX")
        typ, data = conn.search(None, "ALL")
        if typ != "OK":
            return 0
        ids = (data[0] or b"").split()
        for num in reversed(ids[-limit:]):          # newest first, bounded
            typ, msg_data = conn.fetch(num, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            if store_message(raw, provider=provider) is not None:
                stored += 1
    finally:
        try:
            conn.logout()
        except Exception:                           # noqa: BLE001 — best-effort cleanup
            pass
    return stored
