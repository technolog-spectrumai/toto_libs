from django.db import models
from django.utils import timezone


class SyncRule(models.Model):
    """
    Per-model sync policy.

    One rule = one model + one direction.

    If you need bidirectional sync for the same model, create two rules:
      - one with direction="up"
      - one with direction="down"
    """

    DIRECTION_UP = "up"
    DIRECTION_DOWN = "down"

    DIRECTION_CHOICES = [
        (DIRECTION_UP, "Up"),
        (DIRECTION_DOWN, "Down"),
    ]

    CONFLICT_SOURCE_WINS = "source_wins"
    CONFLICT_TARGET_WINS = "target_wins"
    CONFLICT_NEWEST_WINS = "newest_wins"
    CONFLICT_SKIP = "skip"

    CONFLICT_CHOICES = [
        (CONFLICT_SOURCE_WINS, "Source wins"),
        (CONFLICT_TARGET_WINS, "Target wins"),
        (CONFLICT_NEWEST_WINS, "Newest wins"),
        (CONFLICT_SKIP, "Skip conflicts"),
    ]

    platform = models.ForeignKey(
        "core.Platform",
        on_delete=models.CASCADE,
        related_name="sync_rules",
    )

    name = models.CharField(max_length=150)

    model_label = models.CharField(
        max_length=150,
        help_text="Example: core.Theme, accounts.Profile, projects.Project",
    )

    enabled = models.BooleanField(default=True)

    direction = models.CharField(
        max_length=10,
        choices=DIRECTION_CHOICES,
    )

    fields = models.JSONField(
        default=list,
        blank=True,
        help_text="List of field names to sync. Empty means all fields allowed by the adapter.",
    )

    filters = models.JSONField(
        default=dict,
        blank=True,
        help_text='Example: {"active": true, "changed_since_last_sync": true}',
    )

    sync_creates = models.BooleanField(default=True)
    sync_updates = models.BooleanField(default=True)
    sync_deletes = models.BooleanField(default=False)

    conflict_policy = models.CharField(
        max_length=30,
        choices=CONFLICT_CHOICES,
        default=CONFLICT_SOURCE_WINS,
    )

    include_dependencies = models.BooleanField(
        default=False,
        help_text="Include referenced uid-enabled dependency models where supported.",
    )

    last_pushed_at = models.DateTimeField(null=True, blank=True)
    last_pulled_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "sync rule"
        verbose_name_plural = "sync rules"
        ordering = ["platform", "model_label", "direction", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["platform", "model_label", "direction"],
                name="unique_sync_rule_per_platform_model_direction",
            )
        ]

    def __str__(self):
        return f"{self.name} — {self.model_label} — {self.direction}"

    @property
    def app_label(self):
        return self.model_label.split(".", 1)[0] if "." in self.model_label else ""

    @property
    def model_name(self):
        return self.model_label.split(".", 1)[1] if "." in self.model_label else self.model_label

    def mark_synced(self, when=None):
        when = when or timezone.now()

        if self.direction == self.DIRECTION_UP:
            self.last_pushed_at = when
            self.save(update_fields=["last_pushed_at", "updated_at"])
        elif self.direction == self.DIRECTION_DOWN:
            self.last_pulled_at = when
            self.save(update_fields=["last_pulled_at", "updated_at"])

    def mark_pushed(self, when=None):
        self.last_pushed_at = when or timezone.now()
        self.save(update_fields=["last_pushed_at", "updated_at"])

    def mark_pulled(self, when=None):
        self.last_pulled_at = when or timezone.now()
        self.save(update_fields=["last_pulled_at", "updated_at"])


class SyncRun(models.Model):
    """
    One execution of a sync rule.
    """

    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_PARTIAL = "partial"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_PARTIAL, "Partial"),
        (STATUS_FAILED, "Failed"),
    ]

    DIRECTION_UP = SyncRule.DIRECTION_UP
    DIRECTION_DOWN = SyncRule.DIRECTION_DOWN
    DIRECTION_CHOICES = SyncRule.DIRECTION_CHOICES

    platform = models.ForeignKey(
        "core.Platform",
        on_delete=models.CASCADE,
        related_name="sync_runs",
    )

    rule = models.ForeignKey(
        SyncRule,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runs",
    )

    direction = models.CharField(
        max_length=10,
        choices=DIRECTION_CHOICES,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_RUNNING,
    )

    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)

    exported_count = models.PositiveIntegerField(default=0)
    imported_count = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    deleted_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)

    package_hash = models.CharField(
        max_length=64,
        blank=True,
        help_text="SHA-256 hash of the sync package, when available.",
    )

    remote_status_code = models.PositiveIntegerField(null=True, blank=True)
    remote_response = models.JSONField(default=dict, blank=True)

    message = models.TextField(blank=True)

    class Meta:
        verbose_name = "sync run"
        verbose_name_plural = "sync runs"
        ordering = ["-started_at"]

    def __str__(self):
        rule_name = self.rule.name if self.rule else "unknown rule"
        return f"{rule_name} — {self.direction} — {self.status}"

    def finish(self, status, message="", remote_response=None):
        self.status = status
        self.message = message or self.message
        self.finished_at = timezone.now()

        update_fields = ["status", "message", "finished_at"]

        if remote_response is not None:
            self.remote_response = remote_response
            update_fields.append("remote_response")

        self.save(update_fields=update_fields)

    def mark_success(self, message="Sync completed.", remote_response=None):
        self.finish(
            status=self.STATUS_SUCCESS,
            message=message,
            remote_response=remote_response,
        )

    def mark_partial(self, message="Sync partially completed.", remote_response=None):
        self.finish(
            status=self.STATUS_PARTIAL,
            message=message,
            remote_response=remote_response,
        )

    def mark_failed(self, message="Sync failed.", remote_response=None):
        self.finish(
            status=self.STATUS_FAILED,
            message=message,
            remote_response=remote_response,
        )


class SyncObjectRun(models.Model):
    """
    Per-object audit log for a sync run.
    """

    ACTION_EXPORT = "export"
    ACTION_IMPORT = "import"
    ACTION_CREATE = "create"
    ACTION_UPDATE = "update"
    ACTION_DELETE = "delete"
    ACTION_SKIP = "skip"
    ACTION_ERROR = "error"

    ACTION_CHOICES = [
        (ACTION_EXPORT, "Export"),
        (ACTION_IMPORT, "Import"),
        (ACTION_CREATE, "Create"),
        (ACTION_UPDATE, "Update"),
        (ACTION_DELETE, "Delete"),
        (ACTION_SKIP, "Skip"),
        (ACTION_ERROR, "Error"),
    ]

    STATUS_SUCCESS = "success"
    STATUS_SKIPPED = "skipped"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_SUCCESS, "Success"),
        (STATUS_SKIPPED, "Skipped"),
        (STATUS_FAILED, "Failed"),
    ]

    run = models.ForeignKey(
        SyncRun,
        on_delete=models.CASCADE,
        related_name="object_runs",
    )

    model_label = models.CharField(max_length=150)
    uid = models.CharField(max_length=100)

    action = models.CharField(
        max_length=20,
        choices=ACTION_CHOICES,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
    )

    message = models.TextField(blank=True)
    payload = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "sync object run"
        verbose_name_plural = "sync object runs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["model_label", "uid"]),
            models.Index(fields=["action", "status"]),
        ]

    def __str__(self):
        return f"{self.model_label} uid={self.uid} — {self.action} — {self.status}"
