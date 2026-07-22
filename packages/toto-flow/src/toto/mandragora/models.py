from django.db import models
from django.utils import timezone
from django.utils.text import slugify


# ---------------------------------------------------------
#  Base Executable Unit
# ---------------------------------------------------------

class ExecutableUnit(models.Model):
    content = models.TextField(blank=True)
    stdout = models.TextField(blank=True)
    stderr = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        abstract = True


# ---------------------------------------------------------
#  Compute Kernel (generic backend config)
# ---------------------------------------------------------

class ComputeKernel(models.Model):
    name = models.CharField(max_length=255, unique=True)
    env = models.JSONField(null=True, blank=True)
    timeout_ms = models.IntegerField(
        default=5000,
        help_text="Per-cell execution timeout in milliseconds.",
    )
    startup_timeout_ms = models.IntegerField(
        default=120_000,
        help_text=(
            "How long to wait for the kernel process to become ready, in milliseconds. "
            "Increase this for slow Docker environments or when installing many dependencies."
        ),
    )
    auto_close = models.BooleanField(
        default=True,
        help_text="Automatically stop this kernel when the user leaves the notebook page.",
    )
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.name


class KernelDependency(models.Model):
    PENDING = "pending"
    INSTALLING = "installing"
    INSTALLED = "installed"
    FAILED = "failed"

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (INSTALLING, "Installing"),
        (INSTALLED, "Installed"),
        (FAILED, "Failed"),
    ]

    kernel = models.ForeignKey(
        ComputeKernel,
        on_delete=models.CASCADE,
        related_name="kernel_dependencies",
    )
    package_name = models.CharField(max_length=255)
    version_spec = models.CharField(
        max_length=100,
        blank=True,
        help_text='e.g. ">=1.21", "==2.0.0", or blank for latest',
    )
    install_status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=PENDING
    )
    install_log = models.TextField(blank=True)
    installed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "kernel dependencies"
        unique_together = [("kernel", "package_name")]

    def pip_specifier(self):
        return f"{self.package_name}{self.version_spec}" if self.version_spec else self.package_name

    def __str__(self):
        return self.pip_specifier()


# ---------------------------------------------------------
#  Notebook
# ---------------------------------------------------------

class Notebook(models.Model):
    title = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    kernel = models.OneToOneField(
        ComputeKernel,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notebook",
    )
    bucket = models.ForeignKey(
        "vault.Bucket",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notebooks",
        help_text=(
            "Vault bucket attached to this notebook. "
            "Files are injected as vault_files dict so you can open(vault_files['name'])."
        ),
    )

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title) or f"notebook-{self.pk or ''}"
            slug = base
            n = 1
            while Notebook.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


# ---------------------------------------------------------
#  Cell (inherits ExecutableUnit)
# ---------------------------------------------------------

class Cell(ExecutableUnit):
    CODE = "code"
    MARKDOWN = "markdown"

    CELL_TYPES = [
        (CODE, "Code"),
        (MARKDOWN, "Markdown"),
    ]

    notebook = models.ForeignKey(
        Notebook,
        on_delete=models.CASCADE,
        related_name="cells"
    )
    cell_type = models.CharField(max_length=20, choices=CELL_TYPES, default=CODE)
    position = models.PositiveIntegerField(default=0)

    execution_count = models.PositiveIntegerField(default=0)
    rich_output = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["position"]

    def __str__(self):
        return f"{self.cell_type} cell {self.id}"

