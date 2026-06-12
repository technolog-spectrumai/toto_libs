"""Nomad — app-managed Tor onion identity for faros.

faros's `.onion` is published over the Tor control port (see ``tor_control``) from
an ed25519 key that nomad owns. The *secret* lives in a file in the ``nomad_data``
volume (``keystore``); this model is **metadata only** — a history of which
addresses have been the server's identity and who rotated them.

A superuser can mint a fresh identity (retiring the old one) from the Django admin
or the "Onion Identity" section of their profile settings (``migrate_onion``).
This app is installed on faros only, never on portal.
"""
from django.conf import settings
from django.db import models


class OnionIdentity(models.Model):
    """A Tor v3 onion address the server has published (current or retired)."""

    # The 56-char base32 service id (the .onion address without the suffix).
    service_id = models.CharField(max_length=80, unique=True)
    is_active = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now_add=True)
    retired_at = models.DateTimeField(null=True, blank=True)
    # The superuser who triggered the migration that created this identity
    # (null for the first/auto-minted identity).
    migrated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="onion_migrations",
    )

    class Meta:
        ordering = ("-created",)
        verbose_name = "Onion identity"
        verbose_name_plural = "Onion identities"

    @property
    def onion(self) -> str:
        return f"{self.service_id}.onion"

    def __str__(self) -> str:
        return self.onion
