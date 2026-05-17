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
    env = models.JSONField(null=True, blank=True)
    timeout_ms = models.IntegerField(default=5000)
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


# ---------------------------------------------------------
#  Workflow DAG
# ---------------------------------------------------------

class Workflow(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.name


class WorkflowNode(models.Model):
    LAMBDA = "lambda"
    HUMAN = "human"
    SPLIT = "split"
    JOIN = "join"

    NODE_TYPES = [
        (LAMBDA, "Lambda"),
        (HUMAN, "Human"),
        (SPLIT, "Split"),
        (JOIN, "Join"),
    ]

    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="nodes")
    node_type = models.CharField(max_length=20, choices=NODE_TYPES)
    label = models.CharField(max_length=255, blank=True)
    lambda_function = models.ForeignKey(
        LambdaFunction,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="workflow_nodes",
    )
    # Human nodes: {"schema": {...json-schema...}, "output_mapping": {...}}
    # Split/Join: currently unused, reserved for future config
    config = models.JSONField(default=dict, blank=True)
    position_x = models.FloatField(default=0.0)
    position_y = models.FloatField(default=0.0)

    def __str__(self):
        return f"{self.node_type}:{self.id} ({self.label})"


class WorkflowEdge(models.Model):
    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="edges")
    source = models.ForeignKey(
        WorkflowNode, on_delete=models.CASCADE, related_name="outgoing_edges"
    )
    target = models.ForeignKey(
        WorkflowNode, on_delete=models.CASCADE, related_name="incoming_edges"
    )
    branch_key = models.CharField(max_length=255, blank=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        unique_together = [("source", "target")]

    def __str__(self):
        return f"Edge {self.source_id}→{self.target_id} [{self.branch_key or 'default'}]"


class WorkflowRun(models.Model):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (RUNNING, "Running"),
        (PAUSED, "Paused"),
        (COMPLETED, "Completed"),
        (FAILED, "Failed"),
    ]

    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="runs")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    input_data = models.JSONField(default=dict, blank=True)
    output_data = models.JSONField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"Run {self.id} [{self.workflow}] {self.status}"


class WorkflowNodeRun(models.Model):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (RUNNING, "Running"),
        (WAITING, "Waiting for human"),
        (COMPLETED, "Completed"),
        (FAILED, "Failed"),
        (SKIPPED, "Skipped"),
    ]

    workflow_run = models.ForeignKey(
        WorkflowRun, on_delete=models.CASCADE, related_name="node_runs"
    )
    node = models.ForeignKey(
        WorkflowNode, on_delete=models.CASCADE, related_name="node_runs"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    input_data = models.JSONField(null=True, blank=True)
    output_data = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("workflow_run", "node")]

    def __str__(self):
        return f"NodeRun {self.id} ({self.node}) [{self.status}]"


class WorkflowEdgeRun(models.Model):
    workflow_run = models.ForeignKey(
        WorkflowRun, on_delete=models.CASCADE, related_name="edge_runs"
    )
    edge = models.ForeignKey(
        WorkflowEdge, on_delete=models.CASCADE, related_name="edge_runs"
    )
    activated = models.BooleanField(default=False)
    activated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("workflow_run", "edge")]

    def __str__(self):
        state = "activated" if self.activated else "skipped"
        return f"EdgeRun {self.id} (edge {self.edge_id}) [{state}]"


class HumanTask(models.Model):
    PENDING = "pending"
    SUBMITTED = "submitted"

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (SUBMITTED, "Submitted"),
    ]

    node_run = models.OneToOneField(
        WorkflowNodeRun, on_delete=models.CASCADE, related_name="human_task"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    form_schema = models.JSONField(default=dict)
    submitted_data = models.JSONField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"HumanTask {self.id} [{self.status}]"

