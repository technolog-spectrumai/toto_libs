"""One mana, sigils and auras.

There is ONE kind of mana. A person has one balance (:class:`ManaAccount`)
and every change to it is one signed row (:class:`ManaEvent`), whatever put
it there: the hourly regeneration, an administrator's grant, a purchase a
source made, an action that spent it. Nothing here names where mana came
from beyond the event's ``kind``; nothing here knows a ledger or an asset.

What regenerates it:

* an :class:`Aura`, through a community — a community has at most one
  (:class:`CommunityAura`), and many communities may have the same one;
* a :class:`Sigil`, an object of its own with a UUID, a name and an image,
  which an administrator assigns to one person at a time and may revoke.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

ZERO = Decimal("0")


class ManaAccount(models.Model):
    """A person's mana. One row a person, made the first time it is needed."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="mana_account")
    balance = models.DecimalField(max_digits=14, decimal_places=4, default=ZERO)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(balance__gte=0),
                                   name="discipline_mana_never_negative"),
        ]

    def __str__(self):
        return f"{self.user} — {self.balance}"


class ManaKind(models.TextChoices):
    REGEN = "regen", _("Regeneration")
    SIGNUP = "signup", _("First balance")
    SPEND = "spend", _("Spent")
    REFUND = "refund", _("Given back")
    GRANT = "grant", _("Granted")
    PURCHASE = "purchase", _("Bought")


class ManaEvent(models.Model):
    """One change of one person's mana. ``key`` is what makes a change happen
    once: the hour of a regeneration, the row an action was for, a grant's
    own id."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="mana_events")
    delta = models.DecimalField(max_digits=14, decimal_places=4)
    balance_after = models.DecimalField(max_digits=14, decimal_places=4)
    kind = models.CharField(max_length=12, choices=ManaKind.choices)
    key = models.CharField(max_length=190, unique=True)
    #: What was done, for a spend and its refund: the metric, how much of it.
    metric_code = models.CharField(max_length=80, blank=True, default="")
    quantity = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    unit = models.CharField(max_length=20, blank=True, default="")
    #: The row the action was for ("vault.VaultFile", its id), as text: the
    #: library may not point at every app's tables.
    source_type = models.CharField(max_length=80, blank=True, default="")
    source_id = models.CharField(max_length=80, blank=True, default="")
    reason = models.CharField(max_length=255, blank=True, default="")
    #: Who granted it, for a grant.
    by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                           blank=True, related_name="mana_events_made")
    #: What a regeneration was made of: {"aura": "4", "sigils": {uuid: "2"}}.
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(fields=["user", "-created_at"], name="discipline_event_user"),
            models.Index(fields=["source_type", "source_id"], name="discipline_event_source"),
        ]

    def __str__(self):
        return f"{self.user} {self.delta:+} ({self.kind})"


class Aura(models.Model):
    """Regeneration a community gives its members, with no sigil needed.

    An object of its own: several communities may have the same aura, and
    changing it changes them all."""

    name = models.CharField(_("name"), max_length=120, unique=True)
    slug = models.SlugField(unique=True, max_length=120)
    description = models.TextField(blank=True, default="")
    regen_per_hour = models.DecimalField(_("mana an hour"), max_digits=12, decimal_places=4,
                                         default=ZERO)
    #: What regeneration fills to. Empty: the platform's own (discipline.yaml).
    cap = models.DecimalField(_("cap"), max_digits=14, decimal_places=4, null=True, blank=True)
    image = models.ImageField(upload_to="discipline/auras/", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(regen_per_hour__gte=0),
                                   name="discipline_aura_rate_not_negative"),
            models.CheckConstraint(condition=Q(cap__isnull=True) | Q(cap__gte=0),
                                   name="discipline_aura_cap_not_negative"),
        ]

    def __str__(self):
        return self.name


class CommunityAura(models.Model):
    """Which aura a community has: one row a community, so one aura at most."""

    community = models.OneToOneField("socialhub.Community", on_delete=models.CASCADE,
                                     related_name="aura_link")
    aura = models.ForeignKey(Aura, on_delete=models.CASCADE, related_name="community_links")

    def __str__(self):
        return f"{self.community} — {self.aura}"


class Sigil(models.Model):
    """A thing an administrator hands to one person, which generates mana for
    whoever holds it. Revoked, it stops; it is never deleted by a revocation,
    and may be assigned again."""

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(_("name"), max_length=120)
    image = models.ImageField(upload_to="discipline/sigils/", blank=True)
    description = models.TextField(blank=True, default="")
    regen_per_hour = models.DecimalField(_("mana an hour"), max_digits=12, decimal_places=4,
                                         default=ZERO)
    holder = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                               blank=True, related_name="sigils")
    #: False: revoked. A sigil without a holder generates nothing either way.
    active = models.BooleanField(default=True)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                  null=True, blank=True, related_name="sigils_issued")
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name="sigils_revoked")
    assigned_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    #: For what a sigil may come to mean beside regeneration.
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(regen_per_hour__gte=0),
                                   name="discipline_sigil_rate_not_negative"),
        ]

    def __str__(self):
        return self.name

    @property
    def generating(self) -> bool:
        return bool(self.active and self.holder_id)


class SigilAction(models.TextChoices):
    ASSIGNED = "assigned", _("Assigned")
    REVOKED = "revoked", _("Revoked")


class SigilEvent(models.Model):
    """A sigil passing into or out of a person's hands."""

    sigil = models.ForeignKey(Sigil, on_delete=models.CASCADE, related_name="events")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="sigil_events")
    action = models.CharField(max_length=10, choices=SigilAction.choices)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                           blank=True, related_name="sigil_events_made")
    note = models.CharField(max_length=255, blank=True, default="")
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at", "-pk"]

    def __str__(self):
        return f"{self.sigil} {self.action} {self.user}"
