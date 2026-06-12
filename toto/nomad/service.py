"""Orchestration — the public API the management commands, admin and views call.

Three operations:
  - ``ensure_onion``   publish the onion on boot (mint on first ever run, else
                       re-publish the stored key). Idempotent.
  - ``migrate_onion``  mint a fresh identity, retire the old one.
  - ``current_onion``  the active address (for display / deploy.py onion).
"""
from __future__ import annotations

import logging

from django.utils import timezone

from . import keystore, tor_control
from .models import OnionIdentity

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


def ensure_onion() -> str:
    """Publish the onion. Mint+persist on first run, otherwise re-publish the key."""
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
    """Mint a new onion, retire+unpublish the old one. Returns the new service_id."""
    old = keystore.load_key()
    old_service_id = old[0] if old else None

    service_id, private_key = tor_control.mint()
    keystore.save_key(service_id, private_key)
    if old_service_id and old_service_id != service_id:
        tor_control.unpublish(old_service_id)
    _record_active(service_id, migrated_by=triggered_by)
    logger.info("nomad: migrated onion %s.onion -> %s.onion", old_service_id, service_id)
    return service_id


def current_onion() -> str | None:
    """The active onion service_id, or None if nothing is published yet."""
    obj = OnionIdentity.objects.filter(is_active=True).order_by("-created").first()
    if obj:
        return obj.service_id
    existing = keystore.load_key()
    return existing[0] if existing else None
