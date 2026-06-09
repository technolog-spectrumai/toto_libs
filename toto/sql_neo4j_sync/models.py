from django.db import models


class GraphChangeEvent(models.Model):
    ACTION_UPSERT_NODE = "upsert_node"
    ACTION_DELETE_NODE = "delete_node"
    ACTION_RESYNC_LINKS = "resync_links"
    ACTION_RESYNC_LINK_SOURCE = "resync_link_source"
    ACTION_RESYNC_LINK_FULL = "resync_link_full"
    ACTION_UPSERT_JUNCTION = "upsert_junction"
    ACTION_DELETE_JUNCTION = "delete_junction"

    ACTION_CHOICES = [
        (ACTION_UPSERT_NODE, "Upsert node"),
        (ACTION_DELETE_NODE, "Delete node"),
        (ACTION_RESYNC_LINKS, "Resync outgoing links"),
        (ACTION_RESYNC_LINK_SOURCE, "Resync one link source"),
        (ACTION_RESYNC_LINK_FULL, "Resync full link"),
        (ACTION_UPSERT_JUNCTION, "Upsert junction relationship"),
        (ACTION_DELETE_JUNCTION, "Delete junction relationship"),
    ]

    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_DONE = "done"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_DONE, "Done"),
        (STATUS_FAILED, "Failed"),
    ]

    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    graph_label = models.CharField(max_length=128, blank=True)
    model_path = models.CharField(max_length=255, blank=True)
    object_uuid = models.CharField(max_length=64, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    attempts = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Graph Change Event"
        verbose_name_plural = "Graph Change Events"
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["graph_label", "object_uuid"]),
        ]

    def __str__(self):
        target = self.object_uuid or self.payload.get("link_key", "")
        return f"{self.action}: {self.graph_label or target}"


class GraphProjectionPlan(models.Model):
    STATUS_READY = "ready"
    STATUS_APPLYING = "applying"
    STATUS_APPLIED = "applied"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_READY, "Ready"),
        (STATUS_APPLYING, "Applying…"),
        (STATUS_APPLIED, "Applied"),
        (STATUS_FAILED, "Failed"),
    ]

    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_READY,
        db_index=True,
    )
    scope = models.JSONField(default=dict, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    diff = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Graph Projection Plan"
        verbose_name_plural = "Graph Projection Plans"
        ordering = ["-created_at", "-id"]

    @property
    def total_changes(self):
        return self.summary.get("total_changes", 0)

    def __str__(self):
        return f"Projection plan #{self.pk or 'new'} ({self.status})"


class GraphSync(models.Model):
    """
    Dummy model used only to expose a global admin action
    for syncing SQL → Neo4j graph.
    """
    class Meta:
        verbose_name = "Graph Sync"
        verbose_name_plural = "Graph Sync"
        managed = False  # no DB table

    def __str__(self):
        return "Graph Sync"


class GraphSyncSchedule(models.Model):
    STATUS_IDLE = "idle"
    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_IDLE, "Idle"),
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILED, "Failed"),
    ]

    enabled = models.BooleanField(default=False)
    interval_minutes = models.PositiveIntegerField(
        default=30,
        help_text="How often to run the full graph sync (minutes).",
    )
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_run_status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_IDLE,
    )
    last_error = models.TextField(blank=True)

    class Meta:
        verbose_name = "Graph Sync Schedule"
        verbose_name_plural = "Graph Sync Schedule"

    def __str__(self):
        state = "enabled" if self.enabled else "disabled"
        return f"Graph Sync Schedule ({state}, every {self.interval_minutes} min)"

    @classmethod
    def get_config(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
