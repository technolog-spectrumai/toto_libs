"""Connector SQL models.

A :class:`DataConnector` is the *configuration* of an external-API ETL source:
which ``api.Connector`` to call (auth/secrets stay there, in Gervazy), how to
extract records (``extract_config``), and how to map records onto Bento
categories/edge types (``mapping_spec`` — see ``services/mapping.py``).

A :class:`ConnectorRun` is the *audit spine* of one execution: raw payload
archive, request log, stats, error, and a link to the ``IngestProposal`` the
run produced. Graph truth stays in Neo4j; applying a proposal writes through
``toto.ingestor.services.apply`` → ``toto.bento.graph_service``.
"""

from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from toto.api.models import _reject_secret_like_json


class DataConnector(models.Model):
    EXTRACTOR_REST_API = "rest_api"

    # A run stuck in pending/running longer than this (worker killed mid-run,
    # never reached its finally-block save) no longer blocks new runs. Above
    # CELERY_TASK_TIME_LIMIT (30 min), so a live task can never be shadowed.
    STALE_RUN_MAX_AGE = timedelta(hours=2)

    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    description = models.TextField(blank=True)
    http_connector = models.ForeignKey(
        "api.Connector",
        on_delete=models.PROTECT,
        related_name="data_connectors",
        help_text="Outbound API connector supplying base URL + auth (secrets in Gervazy).",
    )
    extractor_kind = models.CharField(
        max_length=40,
        default=EXTRACTOR_REST_API,
        help_text="Extractor plugin key (see services/extract).",
    )
    extract_config = models.JSONField(
        default=dict,
        blank=True,
        validators=[_reject_secret_like_json],
        help_text="Non-secret extraction config: endpoint, params, records_path, pagination.",
    )
    mapping_spec = models.JSONField(
        default=dict,
        blank=True,
        help_text="Declarative record → nodes/relationships mapping (see services/mapping.py).",
    )
    trusted = models.BooleanField(
        default=False,
        help_text="Auto-apply valid elements after a run; the full proposal and "
        "apply log are still recorded for after-the-fact audit.",
    )
    is_active = models.BooleanField(default=True)
    schedule_enabled = models.BooleanField(default=False)
    interval_minutes = models.PositiveIntegerField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="data_connectors",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Data connector"
        verbose_name_plural = "Data connectors"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name) or "data-connector"
            slug = base_slug
            counter = 1
            while type(self).objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        if not self.schedule_enabled:
            # Clear the tick, else re-enabling later inherits a stale past
            # timestamp and fires immediately instead of waiting one interval.
            self.next_run_at = None
        elif self.interval_minutes and self.next_run_at is None:
            self.next_run_at = self.schedule_next(timezone.now())
        super().save(*args, **kwargs)

    def clean(self):
        from .services import mapping as mapping_svc
        from .services.extract import get_extractor

        errors = []
        if self.mapping_spec:
            # Bento checks need the DB/templates; degrade to structural checks
            # if they are unavailable so admin edits never hard-fail on infra.
            try:
                errors += mapping_svc.validate_mapping_spec(self.mapping_spec)
            except Exception:  # noqa: BLE001
                errors += mapping_svc.validate_mapping_spec(self.mapping_spec, check_bento=False)
        try:
            extractor = get_extractor(self.extractor_kind)
        except Exception as exc:  # noqa: BLE001 — unknown kind
            errors.append(str(exc))
        else:
            errors += extractor.validate_config(self.extract_config or {})
        if self.schedule_enabled and not self.interval_minutes:
            errors.append("schedule_enabled requires interval_minutes.")
        if errors:
            raise ValidationError(errors)

    def schedule_next(self, now):
        if not self.interval_minutes:
            return None
        return now + timedelta(minutes=self.interval_minutes)

    def has_run_in_flight(self):
        """A pending/running run blocks new ones — unless it's stale.

        A worker killed mid-run (OOM, SIGKILL, hard time limit) leaves the row
        in ``running`` forever; without the age cutoff that would permanently
        disable the connector (runs are read-only in the admin by design).
        """
        cutoff = timezone.now() - self.STALE_RUN_MAX_AGE
        return self.runs.filter(
            status__in=[ConnectorRun.STATUS_PENDING, ConnectorRun.STATUS_RUNNING],
            created_at__gte=cutoff,
        ).exists()

    def has_unreviewed_run(self):
        return self.runs.filter(status=ConnectorRun.STATUS_REVIEW).exists()


class ConnectorRun(models.Model):
    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_REVIEW = "review"
    STATUS_APPLIED = "applied"
    STATUS_EMPTY = "empty"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running…"),
        (STATUS_REVIEW, "Awaiting review"),
        (STATUS_APPLIED, "Applied"),
        (STATUS_EMPTY, "Empty"),
        (STATUS_FAILED, "Failed"),
    ]

    TRIGGERED_MANUAL = "manual"
    TRIGGERED_SCHEDULE = "schedule"
    TRIGGERED_CHOICES = [
        (TRIGGERED_MANUAL, "Manual"),
        (TRIGGERED_SCHEDULE, "Schedule"),
    ]

    connector = models.ForeignKey(
        DataConnector, on_delete=models.CASCADE, related_name="runs"
    )
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True
    )
    triggered = models.CharField(
        max_length=16, choices=TRIGGERED_CHOICES, default=TRIGGERED_MANUAL
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="connector_runs",
    )
    proposal = models.ForeignKey(
        "ingestor.IngestProposal",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="connector_runs",
    )
    raw_payload_file = models.ForeignKey(
        "vault.VaultFile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Archived raw API payload (all fetched pages).",
    )
    # One entry per fetched page: auth-free url, status_code, records.
    request_log = models.JSONField(default=list, blank=True)
    stats = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    task_id = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["connector", "status"]),
            models.Index(fields=["status", "created_at"]),
        ]
        verbose_name = "Connector run"
        verbose_name_plural = "Connector runs"

    def __str__(self):
        return f"Run #{self.pk or 'new'} of {self.connector} ({self.status})"

    def sync_from_proposal(self, save=True):
        """Mirror a terminal proposal outcome onto the run.

        A reviewer applies the proposal through the untouched ingestor
        endpoints, so the run learns about it here (called from the
        ``IngestProposal`` post_save signal and, belt-and-braces, from views).
        """
        from toto.ingestor.models import IngestProposal

        if self.proposal is None or self.status not in (
            self.STATUS_REVIEW,
            self.STATUS_FAILED,
        ):
            return False
        changed = False
        if self.proposal.status == IngestProposal.STATUS_APPLIED:
            self.status = self.STATUS_APPLIED
            self.finished_at = self.proposal.applied_at or timezone.now()
            self.error = ""
            changed = True
        elif self.proposal.status == IngestProposal.STATUS_FAILED and self.status != self.STATUS_FAILED:
            self.status = self.STATUS_FAILED
            self.error = self.proposal.error or "Apply failed."
            changed = True
        if changed and save:
            self.save(update_fields=["status", "finished_at", "error"])
        return changed
