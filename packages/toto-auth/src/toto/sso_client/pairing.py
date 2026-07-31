"""The consumer half of the act of federation.

One function, :func:`pair`. The admin hands it a pairing code; it works out where
to call, calls the provider, files the secret in the vault, and activates the
connection. No human sees a client secret at any point.

Kept out of ``views.py`` and out of ``admin.py`` so the whole exchange is testable
without a request, and so the admin surface stays a form that calls one thing.

**The outbound call deliberately goes through** ``toto.sso_client.views``'s
``http_requests`` **module attribute.** That is what the two-sided test harness
patches (``sso_core/federation/bridge.py``), so routing the pairing call the same
way as the token exchange is what lets a test drive a real end-to-end pairing
in-process. Importing ``requests`` directly here would make this untestable
without a network.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

from django.db import transaction
from django.utils import timezone

from toto.sso_core import enrollment as wire
from toto.sso_core import vault

logger = logging.getLogger(__name__)

# Same shape as the token exchange (sso_client/views.py): a slow provider is worse
# than a dead one, because the worker is held for the whole wait.
PAIR_TIMEOUT = (5, 15)


class PairingError(Exception):
    """Pairing failed. ``message`` is written to be read by an operator."""

    def __init__(self, message: str, *, code: str = "error"):
        super().__init__(message)
        self.message = message
        self.code = code


def pair(ticket_text: str, *, callback_uri: str, label: str = "") -> "OIDCProviderConfig":  # noqa: F821
    """Redeem a pairing code and store the resulting credentials.

    ``callback_uri`` must be the absolute URL this host will really send at
    ``/authorize``, because the provider registers it verbatim and then compares
    it as an exact string. The caller derives it from the live request rather than
    from configuration, so it cannot drift.
    """
    from django.conf import settings

    from .models import OIDCProviderConfig

    # 1. Refuse before touching the network if a stale environment variable would
    #    make the result a lie. The old code let SSO_CLIENT_SECRET override the
    #    stored value, so a pairing could report success while every login failed
    #    with invalid_client and nothing anywhere said why.
    import os

    if os.environ.get("SSO_CLIENT_SECRET"):
        raise PairingError(
            "SSO_CLIENT_SECRET is set in this host's environment. It is no longer "
            "read, but its presence means somebody expects it to matter — remove "
            "it from the deploy config and .env, then pair again.",
            code="stale_env",
        )

    try:
        ticket = wire.decode_ticket(ticket_text)
    except wire.TicketError as exc:
        raise PairingError(str(exc), code="bad_ticket") from exc

    if not _is_absolute(callback_uri):
        raise PairingError(
            "This host does not know its own public URL, so it cannot tell the "
            "provider where to send people back to.",
            code="bad_callback",
        )

    # 2. The vault must be openable BEFORE the code is spent. A pairing code is
    #    single-use: redeeming it and only then discovering the vault is locked
    #    would burn it and force the operator back to the other platform for a new
    #    one. Fail early instead.
    try:
        vault.load_vault_password()
    except vault.VaultUnavailable as exc:
        raise PairingError(str(exc), code="vault_locked") from exc

    endpoint = f"{ticket.url.rstrip('/')}/sso/enroll/"
    payload = wire.EnrollmentRequest(
        ticket=ticket_text.strip(),
        callback_uri=callback_uri,
        label=label or _own_host(callback_uri),
    ).to_dict()

    grant = _post(endpoint, payload, portal=ticket.url)

    # 3. Store the secret encrypted, then activate — in one transaction, so a
    #    half-paired row can never be left active with no secret.
    with transaction.atomic():
        config = _config_for(grant, portal_url=ticket.url)
        old_secret = config.secret

        secret = vault.store_secret(
            grant.client_secret, name=vault.unique_secret_name(),
        )
        config.secret = secret
        config.label = grant.label or config.label or _own_host(ticket.url)
        config.portal_url = ticket.url
        config.client_id = grant.client_id
        config.scopes = grant.scopes
        config.issuer = grant.issuer
        config.authorization_endpoint = grant.authorization_endpoint
        config.token_endpoint = grant.token_endpoint
        config.userinfo_endpoint = grant.userinfo_endpoint
        config.jwks_uri = grant.jwks_uri
        config.callback_uri = callback_uri
        config.redirect_uris = callback_uri
        config.remote_client_uuid = grant.relying_party_uuid
        config.paired_at = timezone.now()
        config.last_pair_error = ""
        config.active = True
        config.save()

        # Exactly one active connection, the invariant the old model documented
        # and never enforced.
        OIDCProviderConfig.objects.exclude(pk=config.pk).update(active=False)

        # Retire rather than delete: the old ciphertext stays auditable, and
        # EncryptedSecret rows are PROTECT-referenced elsewhere in the suite.
        if old_secret and old_secret.pk != secret.pk:
            vault.retire_secret(old_secret)

    logger.info("Federation pairing complete with %s (client_id=%s)",
                ticket.url, grant.client_id)
    return config


def _config_for(grant, *, portal_url):
    """The row this grant belongs to: the same registration, or a new one.

    Matched on the provider's relying-party UUID, which is stable across
    re-pairings — so rotating a secret updates the existing connection and keeps
    its FederatedIdentity rows, rather than orphaning every linked account behind
    a second row.
    """
    from .models import OIDCProviderConfig

    if grant.relying_party_uuid:
        existing = OIDCProviderConfig.objects.filter(
            remote_client_uuid=grant.relying_party_uuid,
        ).first()
        if existing:
            return existing
    existing = OIDCProviderConfig.objects.filter(
        portal_url=portal_url, client_id=grant.client_id,
    ).first()
    return existing or OIDCProviderConfig()


def _post(endpoint, payload, *, portal):
    """POST to the provider, turning every transport failure into a sentence.

    Routed through ``views.http_requests`` — see the module docstring.
    """
    from . import views

    try:
        response = views.http_requests.post(
            endpoint, json=payload,
            timeout=PAIR_TIMEOUT, verify=views._tls_verify(),
        )
    except Exception as exc:                    # noqa: BLE001 — requests raises several
        # This is the single most common first-pairing failure and it used to
        # surface much later, as a failed login, with no clue the provider was
        # the problem. Naming TLS explicitly matters because a self-signed
        # provider is the normal case for a local pair.
        raise PairingError(
            f"Could not reach {portal} — {exc}. If that platform uses a "
            "self-signed certificate, set SSO_CLIENT_CA_BUNDLE to its CA "
            "certificate (or SSO_CLIENT_VERIFY=0 in development).",
            code="unreachable",
        ) from exc

    if response.status_code >= 400:
        raise PairingError(_explain(response), code="refused")

    try:
        return wire.EnrollmentGrant.from_dict(response.json())
    except (ValueError, wire.TicketError) as exc:
        raise PairingError(
            f"{portal} replied with something this host could not read: {exc}",
            code="bad_reply",
        ) from exc


def _explain(response) -> str:
    """The provider's refusal, in the operator's language."""
    try:
        body = response.json()
    except Exception:                           # noqa: BLE001
        return f"The provider refused the pairing code (HTTP {response.status_code})."

    detail = str(body.get("detail") or "").strip()
    code = str(body.get("error") or "").strip()
    if detail:
        return detail
    return f"The provider refused the pairing code ({code or response.status_code})."


def _is_absolute(url: str) -> bool:
    parsed = urlparse(url or "")
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _own_host(url: str) -> str:
    return (urlparse(url or "").hostname or "").lower()
