"""Turning an ``EmailProvider`` row into a real Django connection, and sending on it.

Separate from ``tasks.py`` on purpose: everything here is testable without a broker, and
``tasks.py`` stays a thin shell that records state around a call into this module.
"""
from __future__ import annotations

from django.core.mail import EmailMultiAlternatives, get_connection

from . import vault
from .models import BACKEND_PATHS, EmailProvider

# The one path a provider must never resolve to. A provider claiming Jess's own backend
# would make send() re-enter the queue, so each dispatched message would enqueue another
# forever. The choice field cannot express it; this is the belt to that braces.
_OWN_BACKEND = "toto.jess.backend.JessEmailBackend"


class NoProvider(RuntimeError):
    """No provider is active, so there is nothing to send through."""


def resolve_provider(message) -> EmailProvider:
    """The provider for this message: its own if set, else whichever is active.

    Read at send time rather than at queue time, deliberately — an operator who fixes a
    misconfigured provider and presses Retry should get the fixed one.
    """
    provider = message.provider or EmailProvider.active_provider()
    if provider is None:
        raise NoProvider(
            "No active email provider. Add one in the admin (Jess → Email providers) "
            "and tick 'active'."
        )
    return provider


def build_connection(provider: EmailProvider, *, session=None):
    """A real Django email connection for ``provider``.

    Raises ``vault.VaultUnavailable`` when the password cannot be read, and ``ValueError``
    if a provider somehow names Jess's own backend.

    ``session`` injects an unlocked, typed-passphrase gervazy session (manual release):
    the SMTP password is decrypted through it instead of through the ambient env session,
    which is what lets a human release held mail without a passphrase living on the box.
    """
    path = BACKEND_PATHS.get(provider.backend)
    if path is None:                                        # pragma: no cover
        raise ValueError(f"Unknown provider backend {provider.backend!r}.")
    if path == _OWN_BACKEND:                                # pragma: no cover
        raise ValueError(
            "A provider cannot send through Jess's own backend — that would queue "
            "every message forever."
        )

    if provider.backend != "smtp":
        # console/dummy/locmem/filebased take none of the transport kwargs, and passing
        # them raises TypeError.
        return get_connection(backend=path, fail_silently=False)

    password = ""
    if provider.secret_id:
        # Raises VaultUnavailable, which the caller records as a failed row rather than
        # letting it become a 500 somewhere. In manual mode a wrong typed passphrase
        # surfaces here as VaultUnavailable before any row is touched.
        password = vault.read_secret(provider.secret, session=session)

    return get_connection(
        backend=path,
        host=provider.host,
        port=provider.port,
        username=provider.username or None,
        password=password or None,
        use_tls=provider.use_tls,
        use_ssl=provider.use_ssl,
        timeout=provider.timeout or None,
        fail_silently=False,
    )


def send_now(message, provider: EmailProvider, *, connection=None) -> None:
    """Send ``message`` through ``provider``, synchronously. Raises on failure.

    Called from the Celery task (normal mode) or from ``release_message`` (manual mode).
    ``connection`` lets a manual release build ONE connection for a whole batch and reuse
    it across every message, so a release is one SMTP handshake rather than one per row.
    """
    from_address = (
        message.from_address
        or provider.from_address
        or None                     # Django then falls back to DEFAULT_FROM_EMAIL
    )

    email = EmailMultiAlternatives(
        subject=message.subject,
        body=message.body,
        from_email=from_address,
        to=list(message.to or []),
        cc=list(message.cc or []),
        bcc=list(message.bcc or []),
        # The message's own list wins; the provider's single address is the fallback.
        reply_to=list(message.reply_to or []) or (
            [provider.reply_to] if provider.reply_to else None
        ),
        headers=dict(message.headers or {}) or None,
        connection=connection or build_connection(provider),
    )
    if message.html_body:
        email.attach_alternative(message.html_body, "text/html")

    # fail_silently is False on the connection, so a refusal raises here and the caller
    # records it verbatim.
    email.send()


def release_message(row, *, session, connection, released_by) -> bool:
    """Send one HELD message under an admin-typed session. Returns True on success.

    Records the outcome on the row exactly as ``tasks.send_mail_message`` does for the
    normal path — SENDING then SENT/FAILED, the provider's error verbatim — so the
    outbox tells the same story whichever way a message left. A failure here fails only
    this row; a batch release keeps going. Never raises for a delivery failure.
    """
    from django.utils import timezone

    from .models import MailMessage

    MailMessage.objects.filter(pk=row.pk).update(
        status=MailMessage.SENDING,
        started_at=timezone.now(),
        attempts=row.attempts + 1,
        error="",
    )
    try:
        provider = resolve_provider(row)
        send_now(row, provider, connection=connection)
    except Exception as exc:                    # noqa: BLE001 — record, never propagate
        MailMessage.objects.filter(pk=row.pk).update(
            status=MailMessage.FAILED, finished_at=timezone.now(),
            error=f"{type(exc).__name__}: {exc}",
        )
        return False

    MailMessage.objects.filter(pk=row.pk).update(
        status=MailMessage.SENT, finished_at=timezone.now(),
        provider_label=(provider.label or ""), error="",
        released_by=released_by, released_at=timezone.now(),
    )
    return True
