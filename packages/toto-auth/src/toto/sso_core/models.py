"""sso_core's one table: the patron-authorized password-recovery ticket.

The first model in this app, and deliberately here rather than in sso_master:
recovery must exist wherever local accounts exist, and ``sso_core`` is the one
auth app installed in **both** federated modes (``auth_config.auth_apps``) —
the same reasoning that moved ``password_reset.py`` here.

A ticket is the email-less half of the reset story (``password_reset.py``
decides which half serves a given request). Its life:

    requested (anonymous, by username)
      → a card on the approver's own profile, shaped like a community invitation
      → approved: a one-time UUID link is minted and shown to the approver ONCE
        (or rejected, and nothing is minted)
      → used: the link's new-password form changed the password, link dead
      → or expired: the request or the link outlived its window

**What is stored is the hash.** The link token is a UUID minted at approval;
the row keeps ``sha256`` of its canonical string and the token itself exists
only in the approver's browser after the one render. A database dump yields
nothing redeemable — the ``SSOFederationInvite`` argument, applied here.

**The approver never touches the password.** The link leads to the new-password
form; approving mints the link, nothing more. What this design cannot prevent
is the link being a bearer credential — whoever holds it can set the password,
the approver included. That is inherent to "patron hands the user a link" and
is bounded the same three ways as a federation invite: single use under a row
lock, a short expiry, and the audit trail naming who approved what and when.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


def _hours(setting_name: str, default: int) -> timedelta:
    return timedelta(hours=max(1, int(getattr(settings, setting_name, default))))


def request_ttl() -> timedelta:
    """How long a pending ticket waits for its approver. Default a week —
    patrons are people, not pagers."""
    return _hours("RECOVERY_TICKET_TTL_HOURS", 7 * 24)


def link_ttl() -> timedelta:
    """How long a minted link stays redeemable. Short: it is a bearer secret
    in somebody's chat scrollback the moment it is handed over."""
    return _hours("RECOVERY_LINK_TTL_HOURS", 24)


def hash_token(token) -> str:
    """The stored form of a link token: sha256 of the canonical UUID string."""
    return hashlib.sha256(str(token).encode("ascii")).hexdigest()


class RecoveryTicket(models.Model):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    USED = "used"
    EXPIRED = "expired"
    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (APPROVED, "Approved"),
        (REJECTED, "Rejected"),
        (USED, "Used"),
        (EXPIRED, "Expired"),
    ]

    # How the approver was chosen — the resolution order in recovery.py.
    RULE_PATRON = "patron"
    RULE_REFERRER = "referrer"
    RULE_STAFF = "staff"
    RULE_CHOICES = [
        (RULE_PATRON, "Patron"),
        (RULE_REFERRER, "Membership referrer"),
        (RULE_STAFF, "Staff queue"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="recovery_tickets",
    )
    # NULL means the staff queue: no patron and no referrer could be resolved,
    # so any staff member may claim it by responding. SET_NULL rather than
    # CASCADE — a departed approver must not take a user's audit-relevant
    # ticket history with them.
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="recovery_tickets_to_approve",
    )
    approver_rule = models.CharField(max_length=12, choices=RULE_CHOICES)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PENDING)

    requested_at = models.DateTimeField(auto_now_add=True)
    request_expires_at = models.DateTimeField()
    responded_at = models.DateTimeField(null=True, blank=True)
    used_at = models.DateTimeField(null=True, blank=True)

    # sha256 of the link token, set at approval. Never the token itself.
    link_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    link_expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-requested_at"]
        constraints = [
            # The race-proof half of request dedupe: two simultaneous requests
            # for one user cannot both file a pending ticket.
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status="pending"),
                name="one_pending_recovery_per_user",
            ),
        ]

    def __str__(self):
        return f"Recovery ticket for {self.user} ({self.status})"

    # -- state tests, all against a caller-supplied now so views ask once --

    def request_expired(self, now=None) -> bool:
        now = now or timezone.now()
        return self.status == self.PENDING and self.request_expires_at <= now

    def link_expired(self, now=None) -> bool:
        now = now or timezone.now()
        return (
            self.status == self.APPROVED
            and self.link_expires_at is not None
            and self.link_expires_at <= now
        )
