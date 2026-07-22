"""Ingestor SQL models.

An :class:`IngestProposal` is a *staged graph patch* — never graph data itself.
It holds the pasted source text and a JSON ``proposal`` (the editable patch of
proposed nodes/relationships with confidence, evidence, validation and approval
state). Graph truth stays in Neo4j; applying a proposal writes through
``toto.bento.graph_service``.

This mirrors ``toto.sql_neo4j_sync.models.GraphProjectionPlan`` (status + summary
+ JSON diff) but is richer because the patch must survive user edits between
generation and apply.
"""

from django.conf import settings
from django.db import models


class IngestProposal(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_READY = "ready"
    STATUS_APPLYING = "applying"
    STATUS_APPLIED = "applied"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_READY, "Ready"),
        (STATUS_APPLYING, "Applying…"),
        (STATUS_APPLIED, "Applied"),
        (STATUS_FAILED, "Failed"),
    ]

    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        db_index=True,
    )
    source_text = models.TextField(blank=True)
    summary = models.JSONField(default=dict, blank=True)
    # The editable staged patch. Shape documented in services/proposal.py.
    proposal = models.JSONField(default=dict, blank=True)
    # temp_id -> uid map + per-element outcome, filled in by the applier.
    apply_result = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ingest_proposals",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Ingest Proposal"
        verbose_name_plural = "Ingest Proposals"
        ordering = ["-created_at", "-id"]

    @property
    def total_changes(self):
        return self.summary.get("total_changes", 0)

    def __str__(self):
        return f"Ingest proposal #{self.pk or 'new'} ({self.status})"
