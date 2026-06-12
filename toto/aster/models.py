"""Aster — the Tor signalling directory.

A minimal address book that lets enigma peers reach each other by a *stable* iroh
NodeId (EndpointId) instead of hand-carrying a fresh ticket each call:

  - ``AsterDevice`` binds a device's NodeId to its owner (SSO user).
  - ``AsterAddress`` holds that device's *current* iroh relay URL, with a TTL so
    stale entries are ignored.

Only relay URLs are stored (never direct socket addresses), so the server never
learns a peer's IP — the address exchange rides Tor, the media rides iroh's relay.
This app is installed on faros only (the Tor server), never on portal.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


def _addr_ttl_seconds() -> int:
    return int(getattr(settings, "ASTER_ADDR_TTL_SECONDS", 300))


class AsterDevice(models.Model):
    """A stable iroh endpoint identity (NodeId), owned by an SSO user.

    A peer runs more than one iroh endpoint — one for gossip chat, one for Vox
    voice — each with its own NodeId. ``kind`` lets a caller resolve the *right*
    one ("vox" vs "gossip") for a given contact.
    """

    KIND_GOSSIP = "gossip"
    KIND_VOX = "vox"
    KIND_DEFAULT = "default"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="aster_devices",
    )
    # iroh EndpointId (ed25519 pubkey, z-base32 ~52 chars). The secret stays on the
    # device, so a NodeId is unforgeable — registration is effectively first-party.
    node_id = models.CharField(max_length=128, unique=True)
    # Which iroh service this endpoint serves (gossip | vox | default).
    kind = models.CharField(max_length=32, default=KIND_DEFAULT)
    label = models.CharField(max_length=120, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-last_seen",)

    def __str__(self) -> str:
        return f"{self.user} · {self.kind} · {self.node_id[:12]}…"


class AsterAddress(models.Model):
    """The device's current reachable relay URL (relay-only — no direct IPs)."""

    device = models.OneToOneField(
        AsterDevice,
        on_delete=models.CASCADE,
        related_name="address",
    )
    relay_url = models.URLField(max_length=300)
    updated_at = models.DateTimeField(auto_now=True)

    def is_fresh(self) -> bool:
        return (timezone.now() - self.updated_at).total_seconds() <= _addr_ttl_seconds()

    def __str__(self) -> str:
        return f"{self.device.node_id[:12]}… → {self.relay_url}"
