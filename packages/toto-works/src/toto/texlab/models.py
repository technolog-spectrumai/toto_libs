from django.db import models


class CompileRun(models.Model):
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

    vault_file = models.ForeignKey(
        "vault.VaultFile",
        on_delete=models.CASCADE,
        related_name="texlab_compile_runs",
    )
    workflow_run = models.ForeignKey(
        "workflows.WorkflowRun",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="compile_runs",
    )
    task_id     = models.CharField(max_length=255, blank=True)
    status      = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    log         = models.TextField(blank=True)
    pdf_url     = models.CharField(max_length=500, blank=True)
    started_at  = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"CompileRun {self.id} [{self.status}] – {self.vault_file}"
