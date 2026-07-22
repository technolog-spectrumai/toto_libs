from django.db import models


class ContractTemplate(models.Model):
    """Admin-editable LaTeX template that converts a ``.contract`` of a given TYPE
    into a PDF.

    The contract's ``<documentType>`` selects the template by :attr:`key`; if none
    matches, the one flagged :attr:`is_default` is used. The ``latex_source`` is
    rendered with the Django template engine (so admins can use ``{{ }}``/``{% %}``)
    against a context of ``{contract, signature_images, content_pdf_filename}`` —
    see :mod:`toto.notarius.latex`.
    """

    name = models.CharField(max_length=120)
    key = models.SlugField(
        max_length=64,
        unique=True,
        help_text="Contract type key — matched against the contract's <documentType>.",
    )
    description = models.TextField(blank=True)
    latex_source = models.TextField(
        help_text=(
            "LaTeX template rendered with the Django template engine. Context: "
            "`contract` (parsed .contract), `signature_images` (list of "
            "{party_name, typed_name, filename}), `content_pdf_filename` (the "
            "embedded original PDF, or empty). Load `{% load notarius_latex %}` and "
            "pipe text through `|latexescape`."
        ),
    )
    is_default = models.BooleanField(
        default=False,
        help_text="Used when no template matches the contract's type. Keep exactly one.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Contract PDF template"
        verbose_name_plural = "Contract PDF templates"

    def __str__(self) -> str:
        return f"{self.name} ({self.key})"

    @classmethod
    def for_type(cls, doc_type: str) -> "ContractTemplate | None":
        """Template matching ``doc_type``; else the default; else the first one."""
        match = cls.objects.filter(key=doc_type).first() if doc_type else None
        return match or cls.objects.filter(is_default=True).first() or cls.objects.first()


class ContractPdfJob(models.Model):
    """Async PDF render of a ``.contract`` — run on a Celery worker (with a
    synchronous fallback when no worker is up). Mirrors texplay.TexPlayJob."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    vault_file = models.ForeignKey(
        "vault.VaultFile", on_delete=models.CASCADE, related_name="contract_pdf_jobs",
    )
    pdf_vault_file = models.ForeignKey(
        "vault.VaultFile", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="contract_pdf_source_jobs",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    log = models.TextField(blank=True)
    celery_task_id = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Contract PDF job"
        verbose_name_plural = "Contract PDF jobs"

    def __str__(self) -> str:
        return f"PDF job #{self.pk} for {self.vault_file_id} ({self.status})"
