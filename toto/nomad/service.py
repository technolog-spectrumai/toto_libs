"""Orchestration — the public API the management commands, admin and views call.

Onion identity:
  - ``ensure_onion``   publish the onion on boot (mint on first ever run, else
                       re-publish the stored key). Idempotent. No-op when disabled.
  - ``migrate_onion``  mint a fresh identity, retire the old one.
  - ``current_onion``  the active address (for display / deploy.py onion).

Reachability switches (per-transport):
  - ``set_onion_enabled``    publish / unpublish the onion (real control-port effect).
  - ``set_clearnet_enabled`` persist intent (the middleware enforces the 404 gate).
  - ``reachability``         current {onion_enabled, clearnet_enabled}.
"""
from __future__ import annotations

import logging

from django.utils import timezone

from . import keystore, tor_control
from .models import NomadSettings, OnionIdentity

logger = logging.getLogger(__name__)


def _record_active(service_id: str, migrated_by=None) -> OnionIdentity:
    """Make ``service_id`` the one active identity, retiring any other active rows."""
    OnionIdentity.objects.filter(is_active=True).exclude(service_id=service_id).update(
        is_active=False, retired_at=timezone.now()
    )
    obj, created = OnionIdentity.objects.get_or_create(
        service_id=service_id,
        defaults={"is_active": True, "migrated_by": migrated_by},
    )
    if not created and not obj.is_active:
        obj.is_active = True
        obj.retired_at = None
        obj.save(update_fields=["is_active", "retired_at"])
    return obj


def ensure_onion() -> str | None:
    """Publish the onion. Mint+persist on first run, otherwise re-publish the key.

    No-op (returns None) when the onion transport is disabled.
    """
    if not NomadSettings.load().onion_enabled:
        logger.info("nomad: onion transport disabled — skipping publish")
        return None

    existing = keystore.load_key()
    if existing:
        _service_id, private_key = existing
        published = tor_control.publish(private_key)
        _record_active(published)
        logger.info("nomad: re-published onion %s.onion", published)
        return published

    service_id, private_key = tor_control.mint()
    keystore.save_key(service_id, private_key)
    _record_active(service_id)
    logger.info("nomad: minted first onion %s.onion", service_id)
    return service_id


def migrate_onion(triggered_by=None) -> str:
    """Mint a new onion, retire+unpublish the old one. Returns the new service_id.

    Migrating implies the onion transport is wanted, so it is (re)enabled.
    """
    old = keystore.load_key()
    old_service_id = old[0] if old else None

    service_id, private_key = tor_control.mint()
    keystore.save_key(service_id, private_key)
    if old_service_id and old_service_id != service_id:
        tor_control.unpublish(old_service_id)
    _record_active(service_id, migrated_by=triggered_by)

    s = NomadSettings.load()
    if not s.onion_enabled:
        s.onion_enabled = True
        s.updated_by = triggered_by
        s.save()

    logger.info("nomad: migrated onion %s.onion -> %s.onion", old_service_id, service_id)
    return service_id


def current_onion() -> str | None:
    """The active onion service_id, or None if nothing is published yet."""
    obj = OnionIdentity.objects.filter(is_active=True).order_by("-created").first()
    if obj:
        return obj.service_id
    existing = keystore.load_key()
    return existing[0] if existing else None


# ---------------------------------------------------------------------------
# Reachability switches
# ---------------------------------------------------------------------------

ONION = "onion"
CLEARNET = "clearnet"


def reachability() -> dict:
    s = NomadSettings.load()
    return {"onion_enabled": s.onion_enabled, "clearnet_enabled": s.clearnet_enabled}


def _guard_last_transport(s: NomadSettings, disabling: str) -> None:
    """Refuse to disable a transport when the other is already off (anti-lockout)."""
    other_on = s.clearnet_enabled if disabling == ONION else s.onion_enabled
    if not other_on:
        raise ValueError(
            "Refusing to disable the last reachable transport — enable the other "
            "transport first so faros stays reachable."
        )


def set_onion_enabled(enabled: bool, by=None) -> None:
    """Enable → (re)publish the onion; disable → unpublish + deactivate identity."""
    s = NomadSettings.load()
    if s.onion_enabled == enabled:
        return
    if not enabled:
        _guard_last_transport(s, ONION)

    s.onion_enabled = enabled
    s.updated_by = by
    s.save()

    if enabled:
        ensure_onion()
    else:
        active = OnionIdentity.objects.filter(is_active=True)
        for ident in active:
            tor_control.unpublish(ident.service_id)
        active.update(is_active=False, retired_at=timezone.now())
        logger.info("nomad: onion transport disabled — unpublished")


def set_clearnet_enabled(enabled: bool, by=None) -> None:
    """Persist the clearnet switch (enforced live by the middleware)."""
    s = NomadSettings.load()
    if s.clearnet_enabled == enabled:
        return
    if not enabled:
        _guard_last_transport(s, CLEARNET)

    s.clearnet_enabled = enabled
    s.updated_by = by
    s.save()
    logger.info("nomad: clearnet transport %s", "enabled" if enabled else "disabled")
