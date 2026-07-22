"""Formica SQL models — the colony's durable state.

The graph carries only decaying *markings* (pheromone props); everything the
colony must remember across cycles — configuration, cycle audit, structural
proposals, foraging statistics — lives here. Mirrors the ingestor/connectors
proposal conventions, but formica ops are **uid-based mutations of existing
elements** (create_edge / update_node / delete_nodes), not a temp-id
create-only patch, hence its own model + apply service.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify

from . import params as params_mod


class Colony(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    description = models.TextField(blank=True)
    enabled = models.BooleanField(default=True)
    paused = models.BooleanField(default=False)
    trusted = models.BooleanField(
        default=False,
        help_text="Auto-apply valid proposal ops after each cycle; the full "
        "proposal and apply log are still recorded for audit.",
    )
    interval_minutes = models.PositiveIntegerField(default=60)
    meta_params = models.JSONField(
        default=dict, blank=True,
        help_text="Overrides of PARAM_SPECS defaults (validated).",
    )
    # Hard exclusions from pruning (labels = bento category slugs).
    protected_labels = models.JSONField(default=list, blank=True)
    protected_uids = models.JSONField(default=list, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Colonies"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name) or "colony"
            slug = base_slug
            counter = 1
            while type(self).objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def clean(self):
        errors = params_mod.validate_params(self.meta_params or {})
        if not isinstance(self.protected_labels, list):
            errors.append("protected_labels must be a list.")
        if not isinstance(self.protected_uids, list):
            errors.append("protected_uids must be a list.")
        if errors:
            raise ValidationError(errors)

    def params(self, overrides=None):
        """Fully-resolved parameter dict (defaults ← colony ← caste overrides)."""
        return params_mod.resolve(self.meta_params, overrides)

    def is_due(self, now):
        if not self.enabled or self.paused:
            return False
        if self.last_run_at is None:
            return True
        from datetime import timedelta

        return self.last_run_at + timedelta(minutes=self.interval_minutes) <= now

    def has_cycle_in_flight(self):
        return self.cycles.filter(
            status__in=[ColonyCycle.STATUS_PENDING, ColonyCycle.STATUS_RUNNING]
        ).exists()


class CasteConfig(models.Model):
    colony = models.ForeignKey(Colony, on_delete=models.CASCADE, related_name="castes")
    caste_key = models.CharField(max_length=40)
    enabled = models.BooleanField(default=True)
    ant_count = models.PositiveIntegerField(default=10)
    param_overrides = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("colony", "caste_key")]
        ordering = ["colony", "caste_key"]

    def __str__(self):
        return f"{self.colony.slug}/{self.caste_key} ×{self.ant_count}"

    def clean(self):
        errors = params_mod.validate_params(self.param_overrides or {})
        if errors:
            raise ValidationError(errors)


class ColonyCycle(models.Model):
    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_ABORTED = "aborted"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running…"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
        (STATUS_ABORTED, "Aborted"),
    ]

    TRIGGERED_BEAT = "beat"
    TRIGGERED_MANUAL = "manual"
    TRIGGERED_CHOICES = [(TRIGGERED_BEAT, "Beat"), (TRIGGERED_MANUAL, "Manual")]

    colony = models.ForeignKey(Colony, on_delete=models.CASCADE, related_name="cycles")
    number = models.PositiveIntegerField()
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True
    )
    triggered_by = models.CharField(
        max_length=16, choices=TRIGGERED_CHOICES, default=TRIGGERED_MANUAL
    )
    # Every cycle is reproducible: the walker seeds its RNG from this.
    rng_seed = models.BigIntegerField(default=0)
    stats = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        unique_together = [("colony", "number")]
        ordering = ["-number"]
        indexes = [models.Index(fields=["colony", "-number"])]

    def __str__(self):
        return f"{self.colony.slug} cycle #{self.number} ({self.status})"


class FormicaProposal(models.Model):
    STATUS_READY = "ready"
    STATUS_APPLYING = "applying"
    STATUS_APPLIED = "applied"
    STATUS_FAILED = "failed"
    STATUS_REJECTED = "rejected"
    STATUS_EXPIRED = "expired"

    STATUS_CHOICES = [
        (STATUS_READY, "Ready"),
        (STATUS_APPLYING, "Applying…"),
        (STATUS_APPLIED, "Applied"),
        (STATUS_FAILED, "Failed"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_EXPIRED, "Expired"),
    ]

    colony = models.ForeignKey(Colony, on_delete=models.CASCADE, related_name="proposals")
    cycle = models.ForeignKey(
        ColonyCycle, on_delete=models.CASCADE, related_name="proposals"
    )
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_READY, db_index=True
    )
    # {"ops": [{op_id, action, params, reason, evidence, validation, approval, result}]}
    ops = models.JSONField(default=dict, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    # {"done": {op_id: {...result}}, "errors": [...]} — idempotent resume map.
    apply_result = models.JSONField(default=dict, blank=True)
    applied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="formica_proposals_applied",
    )
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Proposal #{self.pk or 'new'} ({self.colony.slug}, {self.status})"

    @property
    def op_list(self):
        return (self.ops or {}).get("ops", [])

    @property
    def total_ops(self):
        return len(self.op_list)


class ForagerReport(models.Model):
    """Per-cycle aggregates only — the table grows O(cycles), never O(nodes)."""

    cycle = models.OneToOneField(
        ColonyCycle, on_delete=models.CASCADE, related_name="forager_report"
    )
    # {"label_counts": {...}, "hottest": [{uid, display, ph}], "degree_buckets": {...},
    #  "fill_rates": {...}}
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Forager report for {self.cycle}"
