"""Electorates as data, and the register a vote freezes from them.

Stage 5 of the campaign: voting becomes self-contained. An **Electorate** is
a named, configurable roll — members and weights, equal or unequal — owned by
whatever scope configured it. **Voting rights** are membership; **voting
weight** is ballot power. The concept is the voting half of a cap table made
generic: members, weights, percentages — never "shares", which stay Business
Center's own vocabulary on its own registers.

A vote does not read an electorate live. When voting starts the membership
and weights are copied into **RollEntry** rows — the record date the company
register introduced, now generic — and standing is answered from that copy
for the life of the vote. Editing the electorate afterwards changes future
votes only.

**ConsensusProfile** (stage 7) is the named threshold a vote may select —
Normal Majority, Supermajority — configured by staff only. A vote snapshots
the profile's name AND percentage when it opens, so renaming or retuning a
profile can never rewrite what a past vote required.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _


class ElectorateQuerySet(models.QuerySet):
    def in_scope(self, scope_type: str = "", scope_id: str = ""):
        """Electorates belonging to exactly one scope — the ONLY door."""
        return self.filter(scope_type=scope_type, scope_id=str(scope_id or ""))

    def active(self):
        return self.filter(is_active=True)


class Electorate(models.Model):
    """A named roll: who may vote, and with what power."""

    class Kind(models.TextChoices):
        EQUAL = "equal", _("Equal — one member, one vote")
        WEIGHTED = "weighted", _("Weighted — members carry set weights")

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, blank=True)
    description = models.TextField(blank=True)
    kind = models.CharField(max_length=10, choices=Kind.choices,
                            default=Kind.EQUAL)

    scope_type = models.CharField(max_length=40, blank=True, db_index=True)
    scope_id = models.CharField(max_length=64, blank=True, db_index=True)

    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                   blank=True, on_delete=models.SET_NULL,
                                   related_name="electorates_created")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ElectorateQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["scope_type", "scope_id", "slug"],
                                    name="uniq_electorate_slug_per_scope"),
        ]
        verbose_name = _("electorate")
        verbose_name_plural = _("electorates")

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name) or "electorate"
            slug, counter = base, 1
            siblings = Electorate.objects.in_scope(self.scope_type,
                                                   self.scope_id)
            while siblings.filter(slug=slug).exclude(pk=self.pk).exists():
                counter += 1
                slug = f"{base}-{counter}"
            self.slug = slug
        super().save(*args, **kwargs)

    def total_weight(self) -> int:
        return self.members.aggregate(
            total=models.Sum("weight"))["total"] or 0

    def power_table(self) -> list:
        """Members with their share of the whole — the voting-power view."""
        total = self.total_weight()
        return [{
            "member": member,
            "weight": member.weight,
            "percent": round(member.weight / total * 100, 1) if total else 0,
        } for member in self.members.select_related("user")]


class ElectorateMember(models.Model):
    electorate = models.ForeignKey(Electorate, on_delete=models.CASCADE,
                                   related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             on_delete=models.CASCADE,
                             related_name="electorate_memberships")
    #: Ballot power. On an EQUAL electorate this stays 1 by convention; the
    #: snapshot copies whatever is written here, so the kind is documentation
    #: of intent, not a second enforcement path.
    weight = models.PositiveBigIntegerField(default=1)

    class Meta:
        ordering = ["-weight", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["electorate", "user"],
                                    name="uniq_electorate_member"),
        ]

    def __str__(self):
        return f"{self.user} ({self.weight})"


class RollEntry(models.Model):
    """One line of a vote's frozen register. Generic: a login, a display
    label, a weight — nothing about shares, seats or people-tables.

    ``user`` may be null: a register may name somebody with no account (the
    company case). They are ON the roll — counted in its size and so in the
    turnout denominator — and cannot cast.
    """

    question = models.ForeignKey("polls.Question", on_delete=models.CASCADE,
                                 related_name="roll")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="+")
    label = models.CharField(max_length=150, blank=True)
    weight = models.PositiveBigIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-weight", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["question", "user"],
                                    condition=models.Q(user__isnull=False),
                                    name="uniq_roll_entry_user"),
        ]
        indexes = [
            models.Index(fields=["question", "user"]),
        ]
        verbose_name = _("roll entry")
        verbose_name_plural = _("roll entries")

    def __str__(self):
        return f"{self.label or self.user} ({self.weight})"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError(
                "A register entry is a record date, not a live register.")
        super().save(*args, **kwargs)


class ConsensusProfile(models.Model):
    """A named threshold, staff-configured, snapshotted at vote open.

    The percent is the bar a FOR share must STRICTLY exceed — the same
    comparison Business Center's constitution has always used.
    """

    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=100, unique=True, blank=True)
    percent = models.DecimalField(max_digits=5, decimal_places=2)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["percent", "name"]
        verbose_name = _("consensus profile")
        verbose_name_plural = _("consensus profiles")

    def __str__(self):
        return f"{self.name} ({self.percent}%)"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)
