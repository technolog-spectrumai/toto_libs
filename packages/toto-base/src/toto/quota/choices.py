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
    TRACK = "track", "Track only"
    WARN = "warn", "Warn"
    BLOCK = "block", "Block"


class EventStatus(models.TextChoices):
    RECORDED = "recorded", "Recorded"
    VOIDED = "voided", "Voided"
