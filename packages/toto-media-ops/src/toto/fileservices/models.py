from django.db import models

from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent


class FileServiceRun(models.Model):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED  = "failed"

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (RUNNING, "Running"),
        (SUCCESS, "Success"),
        (FAILED,  "Failed"),
    ]

    service_key = models.CharField(max_length=64)
    owner = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="file_service_runs",
    )
    input_file = models.ForeignKey(
        "vault.VaultFile",
        on_delete=models.CASCADE,
        related_name="file_service_runs",
    )
    # Bucket is denormalised so usage stats survive input-file deletion.
    bucket = models.ForeignKey(
        "vault.Bucket",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="file_service_runs",
    )
    args = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    stdout = models.TextField(blank=True)
    stderr = models.TextField(blank=True)
    output_file_pks = models.JSONField(default=list, blank=True)
    workflow_run = models.ForeignKey(
        "workflows.WorkflowRun",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="file_service_runs",
    )
    task_id = models.CharField(max_length=255, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["bucket", "service_key"]),
            models.Index(fields=["status", "started_at"]),
        ]

    def __str__(self):
        return f"FileServiceRun {self.id} [{self.service_key}/{self.status}]"

    @property
    def output_files(self):
        from toto.vault.models import VaultFile
        if not self.output_file_pks:
            return []
        pks = self.output_file_pks
        by_pk = {f.pk: f for f in VaultFile.objects.filter(pk__in=pks)}
        return [by_pk[pk] for pk in pks if pk in by_pk]


# --------------------------------------------------------------------------- #
# Metering                                                                     #
# --------------------------------------------------------------------------- #
# toto.quota owns no tables, so each metered app declares its own concrete pair
# and the rows live in that app's migrations. See toto/quota/models.py.

class FileserviceUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        verbose_name = "File services usage event"
        verbose_name_plural = "File services usage events"


class FileserviceQuotaPolicy(AbstractQuotaPolicy):
    events = FileserviceUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = "File services quota policy"
        verbose_name_plural = "File services quota policies"
