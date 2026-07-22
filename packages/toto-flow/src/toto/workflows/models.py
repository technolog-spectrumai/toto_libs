from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


REPORT_BLOCK_TYPES = {"card", "chart", "table", "text"}
REPORT_CHART_TYPES = {"bar", "line", "area", "pie"}
REPORT_TYPE_CHOICES = [
    ("card", "Card"),
    ("chart", "Chart"),
    ("table", "Table"),
    ("text", "Text"),
]


def default_report_definition():
    return {
        "version": 1,
        "type": "table",
        "title": "Rows",
        "data": {"path": "rows"},
        "columns": [
            {"key": "name", "label": "Name"},
            {"key": "value", "label": "Value", "format": "number"},
        ],
    }


def validate_report_definition(value):
    if not isinstance(value, dict):
        raise ValidationError("Report definition must be a JSON object.")

    if "pages" not in value:
        _validate_report_block(value, page_key="report", block_index=0)
        return

    pages = value.get("pages")
    if not isinstance(pages, list) or len(pages) != 1:
        raise ValidationError("Report definition must define exactly one page.")

    page_keys: set[str] = set()
    for page_index, page in enumerate(pages):
        if not isinstance(page, dict):
            raise ValidationError(f"Report page #{page_index + 1} must be an object.")

        key = page.get("key") or f"page-{page_index + 1}"
        if key in page_keys:
            raise ValidationError(f'Duplicate report page key: "{key}".')
        page_keys.add(key)

        blocks = page.get("blocks")
        if not isinstance(blocks, list) or len(blocks) != 1:
            raise ValidationError(f'Report page "{key}" must define exactly one block.')

        for block_index, block in enumerate(blocks):
            _validate_report_block(block, page_key=key, block_index=block_index)


def report_definition_block(value):
    if isinstance(value, dict) and "pages" in value:
        pages = value.get("pages") or []
        if pages and isinstance(pages[0], dict):
            blocks = pages[0].get("blocks") or []
            if blocks and isinstance(blocks[0], dict):
                return blocks[0]
    return value if isinstance(value, dict) else {}


def report_definition_type(value):
    return report_definition_block(value).get("type", "table")


def _validate_report_block(block, *, page_key: str, block_index: int):
    label = f'block #{block_index + 1} on page "{page_key}"'
    if not isinstance(block, dict):
        raise ValidationError(f"Report {label} must be an object.")

    block_type = block.get("type")
    if block_type not in REPORT_BLOCK_TYPES:
        raise ValidationError(
            f"Report {label} has unsupported type {block_type!r}. "
            f"Use one of: {', '.join(sorted(REPORT_BLOCK_TYPES))}."
        )

    if block_type == "table":
        columns = block.get("columns")
        if not isinstance(columns, list) or not columns:
            raise ValidationError(f"Report table {label} requires a non-empty columns array.")
        for column in columns:
            if not isinstance(column, dict) or not (column.get("key") or column.get("path")):
                raise ValidationError("Report table columns require key or path.")

    if block_type == "chart":
        chart_type = block.get("chart", "bar")
        if chart_type not in REPORT_CHART_TYPES:
            raise ValidationError(
                f"Report chart {label} has unsupported chart {chart_type!r}. "
                f"Use one of: {', '.join(sorted(REPORT_CHART_TYPES))}."
            )


class LambdaFunction(models.Model):
    function_name = models.CharField(max_length=255, unique=True)
    content = models.TextField(blank=True)
    stdout = models.TextField(blank=True)
    stderr = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    kernel = models.OneToOneField(
        "mandragora.ComputeKernel",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lambda_function",
    )

    def __str__(self):
        return f"LambdaFunction {self.function_name}"


class Workflow(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=280, unique=True, blank=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name) or "workflow"
            slug = base_slug
            counter = 1
            while type(self).objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class ReportTemplate(models.Model):
    name = models.CharField(max_length=180, unique=True)
    slug = models.SlugField(max_length=200, unique=True, blank=True)
    report_type = models.CharField(max_length=20, choices=REPORT_TYPE_CHOICES, default="table")
    description = models.TextField(blank=True)
    definition = models.JSONField(
        default=default_report_definition,
        blank=True,
        validators=[validate_report_definition],
        help_text=(
            "JSON report syntax for one visualization. "
            "Use {type:'chart', data:{path:'series'}, x:'label', y:'value'} or "
            "{type:'table', data:{path:'rows'}, columns:[...]}. "
            "Values can use {'path': 'metrics.total'}."
        ),
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        self.report_type = report_definition_type(self.definition)
        if not self.slug:
            base_slug = slugify(self.name) or "report-template"
            slug = base_slug
            counter = 1
            while type(self).objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class WorkflowNode(models.Model):
    LAMBDA = "lambda"
    SPLIT = "split"
    JOIN = "join"
    REPORT = "report"
    PREDEFINED_TASK = "predefined_task"

    NODE_TYPES = [
        (LAMBDA, "Lambda"),
        (SPLIT, "Split"),
        (JOIN, "Join"),
        (REPORT, "Report"),
        (PREDEFINED_TASK, "Predefined Task"),
    ]

    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="nodes")
    node_type = models.CharField(max_length=20, choices=NODE_TYPES)
    label = models.CharField(max_length=255, blank=True)
    task_name = models.CharField(max_length=255, blank=True)
    lambda_function = models.ForeignKey(
        "workflows.LambdaFunction",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="workflow_nodes",
    )
    report_template = models.ForeignKey(
        ReportTemplate,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="workflow_nodes",
    )
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
        constraints = [
            models.UniqueConstraint(
                fields=["source", "target"],
                name="unique_workflow_edge",
            )
        ]

    def __str__(self):
        return f"Edge {self.source_id}→{self.target_id} [{self.branch_key or 'default'}]"


class WorkflowRun(models.Model):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (RUNNING, "Running"),
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
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (RUNNING, "Running"),
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
    celery_task_id = models.CharField(max_length=255, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workflow_run", "node"],
                name="unique_workflow_node_run",
            )
        ]

    def __str__(self):
        return f"NodeRun {self.id} ({self.node}) [{self.status}]"


class Report(models.Model):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"

    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (PUBLISHED, "Published"),
        (ARCHIVED, "Archived"),
    ]

    template = models.ForeignKey(
        ReportTemplate,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reports",
    )
    workflow_run = models.ForeignKey(
        WorkflowRun,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reports",
    )
    source_node_run = models.ForeignKey(
        WorkflowNodeRun,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reports",
    )
    title = models.CharField(max_length=220)
    slug = models.SlugField(max_length=240, unique=True, blank=True)
    report_type = models.CharField(max_length=20, choices=REPORT_TYPE_CHOICES, default="table")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PUBLISHED)
    definition = models.JSONField(
        default=default_report_definition,
        validators=[validate_report_definition],
        help_text="Snapshot of the report template definition used to generate this report.",
    )
    data = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        self.report_type = report_definition_type(self.definition)
        if not self.slug:
            base_slug = slugify(self.title) or "report"
            slug = base_slug
            counter = 1
            while type(self).objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class ReportPage(models.Model):
    report = models.ForeignKey(Report, on_delete=models.CASCADE, related_name="pages")
    key = models.SlugField(max_length=120)
    title = models.CharField(max_length=180)
    order = models.PositiveIntegerField(default=0)
    blocks = models.JSONField(default=list, blank=True)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["report", "key"],
                name="unique_report_page_key",
            )
        ]

    def __str__(self):
        return f"{self.report}: {self.title}"


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
        constraints = [
            models.UniqueConstraint(
                fields=["workflow_run", "edge"],
                name="unique_workflow_edge_run",
            )
        ]

    def __str__(self):
        state = "activated" if self.activated else "skipped"
        return f"EdgeRun {self.id} (edge {self.edge_id}) [{state}]"
