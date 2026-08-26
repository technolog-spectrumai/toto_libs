"""Abstract bases for per-app usage metering and limits.

This app owns no tables. Each app that wants to meter something declares its
own concrete pair, so the rows live in that app's database tables, in that
app's migrations, and go away with it::

    from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent

    class VaultUsageEvent(AbstractUsageEvent):
        pass

    class VaultQuotaPolicy(AbstractQuotaPolicy):
        events = VaultUsageEvent

That is the whole opt-in. Callers then name the concrete policy and the API
finds the event model through it::

    check_quota(VaultQuotaPolicy, "storage.request", 1, request.user)

Two things the per-app split buys that one shared table could not:

* ``idempotency_key`` can be genuinely unique (partial, ignoring blanks), so a
  duplicate record is refused by the database rather than by a racy
  check-then-insert;
* the subject is a real FK, so deleting a user cascades their usage away
  instead of orphaning rows that still sum into the totals.

Names in ``Meta.constraints``/``Meta.indexes`` must interpolate ``%(class)s``
(Django requires uniqueness across the subclasses), and index names are capped
at 30 characters — hence the terse suffixes.
"""

from __future__ import annotations

from typing import ClassVar

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .choices import EventStatus, Mode, Period

__all__ = [
    "AbstractQuotaPolicy",
    "AbstractUsageEvent",
    "EventStatus",
    "Mode",
    "Period",
]


class AbstractQuotaPolicy(models.Model):
    """A limit on one metric, for everybody.

    One row per metric, and it applies to every user. Nothing is limited until
    a policy exists — an unmetered metric is free, which is what makes the free
    tier expressible as an absence rather than a magic number.

    **There is no per-person override, deliberately.** There used to be: a row
    naming a user beat the default. It went the way every other
    name-an-individual mechanism on this platform went, and for the same reason
    — variation belongs to institutions, not to people. Headroom for one person
    then lived on ``socialhub.Station.limit_multiplier``, and that went too when
    Stations were removed in 8/2026. Limits are flat for everybody now; if
    per-institution headroom is ever wanted again, ``quota/api.py::
    effective_limit`` is the one place it goes.
    """

    #: The concrete AbstractUsageEvent subclass this policy meters. Set it on
    #: the subclass; the API reads it rather than taking two model arguments.
    events: ClassVar[type | None] = None

    name = models.CharField(max_length=255, blank=True)
    metric_code = models.CharField(max_length=100, db_index=True)

    limit = models.DecimalField(max_digits=30, decimal_places=10)
    unit = models.CharField(max_length=100, blank=True)
    period = models.CharField(max_length=20, choices=Period.choices, default=Period.DAILY)
    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.BLOCK)

    active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["metric_code"]
        constraints = [
            models.UniqueConstraint(
                fields=["metric_code"],
                name="%(app_label)s_%(class)s_one_per_metric",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.metric_code} — {self.limit} {self.unit}/{self.period}"

    def is_active_now(self, at=None) -> bool:
        now = at or timezone.now()
        if not self.active:
            return False
        if self.starts_at and self.starts_at > now:
            return False
        if self.ends_at and self.ends_at <= now:
            return False
        return True


class AbstractUsageEvent(models.Model):
    """One recorded usage action.

    Events are the only record of consumption — there is no counter to fall out
    of step with them. A voided event stops counting without being deleted, so
    the history stays honest.
    """

    metric_code = models.CharField(max_length=100)
    quantity = models.DecimalField(max_digits=30, decimal_places=10)
    unit = models.CharField(max_length=100, blank=True)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
    )

    source_type = models.CharField(max_length=100, blank=True)
    source_id = models.CharField(max_length=255, blank=True)
    source_label = models.CharField(max_length=255, blank=True)

    # Blank means "do not deduplicate this one"; anything else must be unique.
    idempotency_key = models.CharField(max_length=512, blank=True)

    status = models.CharField(
        max_length=20, choices=EventStatus.choices, default=EventStatus.RECORDED
    )
    occurred_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        abstract = True
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["metric_code", "occurred_at"], name="%(class)s_mtime"),
            models.Index(fields=["user", "metric_code"], name="%(class)s_umetric"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["idempotency_key"],
                condition=~Q(idempotency_key=""),
                name="%(app_label)s_%(class)s_idem",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.metric_code} × {self.quantity} [{self.user or 'anonymous'}]"
