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


def build_connection(provider: EmailProvider):
    """A real Django email connection for ``provider``.

    Raises ``vault.VaultUnavailable`` when the password cannot be read, and ``ValueError``
    if a provider somehow names Jess's own backend.
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
        # Raises VaultUnavailable, which the task records as a failed row rather than
        # letting it become a 500 somewhere.
        password = vault.read_secret(provider.secret)

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


def send_now(message, provider: EmailProvider) -> None:
    """Send ``message`` through ``provider``, synchronously. Raises on failure.

    Only ever called from the Celery task (or a test). Nothing in a request path reaches
    here — that is the whole point of the queueing backend.
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
        connection=build_connection(provider),
    )
    if message.html_body:
        email.attach_alternative(message.html_body, "text/html")

    # fail_silently is False on the connection, so a refusal raises here and the task
    # records it verbatim.
    email.send()
