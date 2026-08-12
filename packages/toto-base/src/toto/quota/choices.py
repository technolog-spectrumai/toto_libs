"""Enumerations, kept apart from the models.

``models.py`` cannot be imported before the app registry is ready — defining a
Model subclass asks for its containing app config. These are plain
``TextChoices``, so they carry no such constraint and the service layer can
import them at module scope.
"""

from __future__ import annotations

from django.db import models


class Period(models.TextChoices):
    DAILY = "daily", "Daily"
    WEEKLY = "weekly", "Weekly"
    MONTHLY = "monthly", "Monthly"
    YEARLY = "yearly", "Yearly"
    LIFETIME = "lifetime", "Lifetime"


class Mode(models.TextChoices):
    """What happens when a limit is passed.

    ``WARN`` is **retained but never offered**. ``api.check_quota`` reads
    ``policy.mode != Mode.BLOCK: return`` — so WARN and TRACK are byte-identical
    at runtime and nothing anywhere warns. It stayed in the picker for a long
    time as a third option with no behaviour, which is worse than a missing
    feature: an operator would choose it and believe something had been armed.

    The value survives so rows already storing it keep loading and rendering,
    and so a future warning path can adopt it without a migration. It simply
    cannot be *chosen* any more — see :meth:`editable_choices`.
    """

    TRACK = "track", "Track only"
    WARN = "warn", "Warn"
    BLOCK = "block", "Block"

    @classmethod
    def editable_choices(cls):
        """The modes a person may pick — everything that actually does something."""
        return [(value, label) for value, label in cls.choices if value != cls.WARN]


class EventStatus(models.TextChoices):
    RECORDED = "recorded", "Recorded"
    VOIDED = "voided", "Voided"
