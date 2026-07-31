"""Minting and redeeming federation pairing invites, on the provider side.

Separate from ``views.py`` so the whole protocol is testable without HTTP, and
separate from ``provisioning.py`` because that module serves the *host
configuration* path (Grafana, Gitea) which deliberately keeps working unchanged.

The redemption in :func:`redeem` is the security boundary of this feature. Every
check in it exists for a reason named in the comments; none of them are
defensive-programming noise.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from urllib.parse import urlparse

from django.db import transaction
from django.utils import timezone

from toto.sso_core import enrollment as wire

from .models import (
    DEFAULT_INVITE_TTL,
    RESERVED_CLIENT_IDS,
    SSOFederationInvite,
    SSORelyingParty,
)

logger = logging.getLogger(__name__)


class EnrollmentError(Exception):
    """A redemption was refused. ``code`` is the machine-readable reason.

    The codes are a closed set so the consumer can react to them and the docs can
    enumerate them: ``invalid_ticket``, ``expired``, ``already_redeemed``,
    ``revoked``, ``host_mismatch``, ``bad_proof``, ``bad_request``.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class MintedInvite:
    invite: SSOFederationInvite
    ticket: str          # shown once, then never recoverable
    relying_party: SSORelyingParty


def mint(
    *,
    expected_host: str,
    provider_url: str,
    label: str = "",
    scopes: str = "openid email profile",
    trusted: bool = True,
    ttl=None,
    created_by=None,
    relying_party: SSORelyingParty | None = None,
) -> MintedInvite:
    """Create a pairing code, for a new platform or an existing one.

    Pass ``relying_party`` to **re-pair**: the invite hangs off the row that is
    already there, so redemption updates it in place and its UUID never moves.
    That matters more than it looks — ``SSOAccessToken.client`` and
    ``SSOAuthorizationCode.client`` both point at it, so a new row would silently
    invalidate every live session while appearing to succeed. Re-pairing is
    surfaced as an action on the relying party precisely so an operator never has
    to know that minting fresh and re-pairing are different operations.

    A new relying party is created **inactive and with no redirect URI**, because
    neither is known until the far side redeems: only the consumer knows the exact
    callback path it will send, and an exact-string mismatch there is the single
    most common federation misconfiguration in this codebase. Inactive also means
    an abandoned invite leaves nothing usable behind.
    """
    from django.conf import settings

    from toto.sso_core import enrollment as core

    expected_host = _normalise_host(expected_host)
    if not expected_host:
        raise EnrollmentError("bad_request", "An expected hostname is required.")
    if not (provider_url or "").strip():
        raise EnrollmentError(
            "bad_request",
            "This platform has no public URL configured, so it cannot tell the "
            "other side where to call back. Set PLATFORM_DOMAIN.",
        )

    secret = core.new_secret()
    ticket = core.encode_ticket(provider_url.rstrip("/"), secret)
    handle = hashlib.sha256(secret).hexdigest()

    with transaction.atomic():
        if relying_party is None:
            relying_party = SSORelyingParty.objects.create(
                name=label or expected_host,
                client_id=_unique_client_id(label or expected_host),
                redirect_uris="",
                allowed_scopes=scopes,
                trusted=trusted,
                active=False,
                pairing_managed=True,
            )
        invite = SSOFederationInvite.objects.create(
            relying_party=relying_party,
            secret_sha256=handle,
            ticket_prefix=ticket[:8],
            deployment_mac=core.deployment_mac(
                getattr(settings, "FEDERATION_KEY", "") or "", handle,
            ),
            expected_host=expected_host,
            granted_scopes=scopes,
            granted_trusted=trusted,
            expires_at=timezone.now() + (ttl or DEFAULT_INVITE_TTL),
            created_by=created_by if getattr(created_by, "is_authenticated", False) else None,
        )
    return MintedInvite(invite=invite, ticket=ticket, relying_party=relying_party)


def redeem(request_data: dict, *, source_ip=None) -> wire.EnrollmentGrant:
    """Turn a valid pairing code into live credentials. The security boundary.

    Raises :class:`EnrollmentError` for every refusal; the caller maps ``code`` to
    an HTTP status and never echoes the ticket back.

    Failed attempts are recorded **after** the transaction has unwound, in
    :func:`_record_attempt`. Bumping the counter inside the atomic block and then
    raising would roll the counter back along with everything else — the audit
    trail would record only successes, which is exactly backwards for a field
    whose job is to show somebody guessing.
    """
    try:
        payload = wire.EnrollmentRequest.from_dict(request_data)
    except wire.TicketError as exc:
        raise EnrollmentError("bad_request", str(exc)) from exc

    try:
        ticket = wire.decode_ticket(payload.ticket)
    except wire.TicketError as exc:
        raise EnrollmentError("invalid_ticket", str(exc)) from exc

    callback_host = _normalise_host(payload.callback_uri)
    if not callback_host:
        raise EnrollmentError("bad_request", "The callback URI is not a valid absolute URL.")

    try:
        return _redeem(payload, ticket, callback_host, source_ip)
    except EnrollmentError as exc:
        _record_attempt(ticket.secret_sha256, exc.code)
        raise


def _redeem(payload, ticket, callback_host, source_ip) -> wire.EnrollmentGrant:
    # The lookup is by HASH, so the secret itself is never in a query, a log or an
    # index. A miss is indistinguishable from a wrong secret, which is what we
    # want to tell a caller.
    with transaction.atomic():
        invite = (
            SSOFederationInvite.objects
            .select_for_update()                    # serialises concurrent redemptions
            .select_related("relying_party")
            .filter(secret_sha256=ticket.secret_sha256)
            .first()
        )
        if invite is None:
            raise EnrollmentError("invalid_ticket", "That pairing code is not recognised.")

        # State first, so a redeemed or revoked code cannot be probed for its
        # other properties.
        state = invite.state
        if state == "revoked":
            _fail("revoked", "That pairing code was revoked.")
        if state == "redeemed":
            _fail(
                "already_redeemed",
                "That pairing code has already been used. Pairing codes are "
                "single-use — mint a new one.",
            )
        if state == "expired":
            _fail("expired", "That pairing code has expired. Mint a new one.")

        # Is this invite even ours? See wire.deployment_mac: a restored backup
        # brings another deployment's pending rows with it. Skipped when either
        # side has no key, so an unconfigured host can still federate.
        from django.conf import settings

        key = getattr(settings, "FEDERATION_KEY", "") or ""
        if key and invite.deployment_mac:
            expected = wire.deployment_mac(key, ticket.secret_sha256)
            if not wire.macs_match(expected, invite.deployment_mac):
                _fail(
                    "invalid_ticket",
                    "That pairing code was not issued by this platform.",
                )

        # The expected-host pin. This is what replaces the human confirmation
        # step a headless server cannot have — see SSOFederationInvite's
        # docstring. Named explicitly in the error because it is an operator
        # mistake worth stating rather than a security detail worth hiding: an
        # attacker who got this far already knows whether their host matched.
        if callback_host != invite.expected_host:
            _fail(
                "host_mismatch",
                f"This pairing code was issued for {invite.expected_host}, but the "
                f"callback URI is on {callback_host}.",
            )

        relying_party = SSORelyingParty.objects.select_for_update().get(
            pk=invite.relying_party_id,
        )

        # Union rather than replace: during a domain move the old URI must keep
        # working until the far side has actually cut over, and redirect_uris is
        # compared as an exact string with no tolerance.
        uris = relying_party.redirect_uri_list()
        if payload.callback_uri not in uris:
            uris.append(payload.callback_uri)
        relying_party.redirect_uris = "\n".join(uris)

        if payload.label:
            relying_party.name = payload.label
        relying_party.allowed_scopes = invite.granted_scopes
        relying_party.trusted = invite.granted_trusted
        relying_party.active = True
        relying_party.pairing_managed = True
        relying_party.paired_at = timezone.now()
        client_secret = relying_party.rotate_client_secret()
        relying_party.save()

        invite.attempt_count += 1
        invite.redeemed_at = timezone.now()
        invite.redeemed_ip = source_ip
        invite.redeemed_callback_uri = payload.callback_uri
        invite.last_error = ""
        invite.save()

    logger.info(
        "Federation pairing redeemed for %s (client_id=%s) from %s",
        invite.expected_host, relying_party.client_id, source_ip or "unknown",
    )
    return _grant(relying_party, client_secret, ticket.url)


# -- internals ------------------------------------------------------------


def _fail(code: str, message: str):
    """Refuse. The attempt is recorded by :func:`_record_attempt` after unwinding."""
    raise EnrollmentError(code, message)


def _record_attempt(secret_sha256: str, code: str) -> None:
    """Count a failed redemption, in its own transaction.

    Runs after the redemption transaction has rolled back, so this is the only
    thing that survives a refusal. ``F()`` rather than read-modify-write because
    several attempts may be in flight and the count is the whole point.

    Never raises: a failure to write the audit row must not turn a clean 4xx into
    a 500.
    """
    from django.db.models import F

    try:
        SSOFederationInvite.objects.filter(secret_sha256=secret_sha256).update(
            attempt_count=F("attempt_count") + 1, last_error=code,
        )
    except Exception:                       # noqa: BLE001
        logger.warning("Could not record a failed enrollment attempt", exc_info=True)


def _grant(relying_party, client_secret: str, provider_url: str) -> wire.EnrollmentGrant:
    """Everything the consumer needs, resolved here rather than guessed there."""
    from .services import get_issuer, get_public_base_url

    base = (get_public_base_url() or provider_url).rstrip("/")
    try:
        issuer = get_issuer()
    except Exception:                       # noqa: BLE001 — no Platform yet
        issuer = base
    return wire.EnrollmentGrant(
        client_id=relying_party.client_id,
        client_secret=client_secret,
        issuer=issuer,
        authorization_endpoint=f"{base}/sso/authorize/",
        token_endpoint=f"{base}/sso/token/",
        userinfo_endpoint=f"{base}/sso/userinfo/",
        jwks_uri=f"{base}/sso/jwks.json",
        scopes=relying_party.allowed_scopes,
        relying_party_uuid=str(relying_party.pk),
        label=relying_party.name,
    )


def _normalise_host(value: str) -> str:
    """The hostname of a URL, or of a bare host. Lowercased, port stripped.

    Accepts both because an admin types ``studio.example.com`` while the consumer
    sends a full callback URL; both must compare equal. The port is dropped so a
    host that fronts on 443 and calls back from behind a proxy still matches.
    """
    value = (value or "").strip().lower()
    if not value:
        return ""
    if "://" in value:
        return (urlparse(value).hostname or "").lower()
    return value.split("/")[0].split(":")[0]


def _unique_client_id(label: str) -> str:
    """A readable client_id that does not collide and cannot steal a sidecar's.

    Readable matters: it appears in the consumer's config, in log lines and in
    error messages, and ``studio`` is far easier to reason about than 24 random
    characters.

    **Reserved ids are refused, and this is not theoretical.** The slug of an
    invite labelled "Gitea" is ``gitea``. On any database where that row does not
    happen to exist yet — a fresh install, anything after ``RESET=1``, or a
    deployment with the service switched off — the invite would take the id and
    mark the row ``pairing_managed``. From then on ``create_relying_party`` raises
    on it forever (``provisioning.py``), and because the provisioning loop had no
    guard, **every sidecar after it in the loop silently stopped being
    provisioned**. Falling through to the suffixed branch costs six characters and
    closes it.
    """
    import re
    import secrets as _secrets

    from django.conf import settings

    base = re.sub(r"[^a-z0-9]+", "-", (label or "platform").lower()).strip("-") or "platform"
    base = base[:40]

    # Sidecars this library ships support for, plus whatever else the host has
    # declared. Read at call time rather than imported once: a host may add a
    # declared party without this module being reloaded.
    reserved = set(RESERVED_CLIENT_IDS)
    for spec in (getattr(settings, "SSO_RELYING_PARTIES", None) or []):
        declared = (spec.get("client_id") or "").strip().lower()
        if declared:
            reserved.add(declared)

    if base not in reserved and not SSORelyingParty.objects.filter(client_id=base).exists():
        return base
    while True:
        candidate = f"{base}-{_secrets.token_hex(3)}"
        if candidate in reserved:
            continue
        if not SSORelyingParty.objects.filter(client_id=candidate).exists():
            return candidate
