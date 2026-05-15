from django.db import models
from django.utils import timezone
from django.utils.text import slugify


# ---------------------------------------------------------
#  Base Executable Unit (shared by Cell + LambdaFunction)
# ---------------------------------------------------------

class ExecutableUnit(models.Model):
    """
    Minimal shared base class for Cell and LambdaFunction.
    """

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
    """
    Generic compute backend used by Notebook or LambdaFunction.
    Stores environment variables and execution timeout.
    """
    name = models.CharField(max_length=255, unique=True)
    # Environment variables injected into the kernel
    env = models.JSONField(null=True, blank=True)
    # Timeout in milliseconds for cell execution
    timeout_ms = models.IntegerField(default=5000)
    # Pip dependencies installed inside the kernel
    dependencies = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    def __str__(self):
        return self.name


# ---------------------------------------------------------
#  Notebook
# ---------------------------------------------------------

class Notebook(models.Model):
    title = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    # Notebook uses a compute kernel
    kernel = models.OneToOneField(
        ComputeKernel,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notebook"
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

    # Notebook-only execution metadata
    execution_count = models.PositiveIntegerField(default=0)
    rich_output = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["position"]

    def __str__(self):
        return f"{self.cell_type} cell {self.id}"


class LambdaFunction(ExecutableUnit):
    function_name = models.CharField(max_length=255, unique=True)

    # LambdaFunction also uses a compute kernel
    kernel = models.OneToOneField(
        ComputeKernel,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lambda_function"
    )

    def __str__(self):
        return f"LambdaFunction {self.function_name}"

