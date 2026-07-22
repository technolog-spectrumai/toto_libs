from django.db import models
from django.utils.translation import gettext_lazy as _


class TexPlayJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        RUNNING = "running", _("Running")
        SUCCESS = "success", _("Success")
        FAILED  = "failed",  _("Failed")

    vault_file = models.ForeignKey(
        "vault.VaultFile",
        on_delete=models.CASCADE,
        related_name="texplay_jobs",
    )
    pdf_vault_file = models.ForeignKey(
        "vault.VaultFile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="texplay_source_jobs",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    workflow_run = models.ForeignKey(
        "workflows.WorkflowRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="texplay_jobs",
    )
    log = models.TextField(blank=True)
    celery_task_id = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "texplay"
        ordering = ["-created_at"]
        verbose_name = _("TeX Play Job")
        verbose_name_plural = _("TeX Play Jobs")

    def __str__(self):
        return f"TexPlayJob #{self.pk} [{self.status}]"
