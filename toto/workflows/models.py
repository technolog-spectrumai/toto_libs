from django.db import models
from django.utils import timezone


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
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.name


class WorkflowConnector(models.Model):
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    API_REQUEST = "api_request"
    PEOPLE_READ = "people_read"
    SOCIALHUB_READ = "socialhub_read"
    LOCATIONS_READ = "locations_read"

    CONNECTOR_TYPES = [
        (FILE_READ, "File read"),
        (FILE_WRITE, "File write"),
        (API_REQUEST, "API request"),
        (PEOPLE_READ, "People read"),
        (SOCIALHUB_READ, "Socialhub read"),
        (LOCATIONS_READ, "Locations read"),
    ]

    name = models.CharField(max_length=255, unique=True)
    connector_type = models.CharField(max_length=40, choices=CONNECTOR_TYPES)
    config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.name} ({self.connector_type})"


class WorkflowNode(models.Model):
    LAMBDA = "lambda"
    HUMAN = "human"
    SPLIT = "split"
    JOIN = "join"
    CONNECTOR = "connector"

    NODE_TYPES = [
        (LAMBDA, "Lambda"),
        (HUMAN, "Human"),
        (SPLIT, "Split"),
        (JOIN, "Join"),
        (CONNECTOR, "Connector"),
    ]

    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="nodes")
    node_type = models.CharField(max_length=20, choices=NODE_TYPES)
    label = models.CharField(max_length=255, blank=True)
    lambda_function = models.ForeignKey(
        "workflows.LambdaFunction",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="workflow_nodes",
    )
    connector = models.ForeignKey(
        WorkflowConnector,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="workflow_nodes",
    )
    # Human nodes: {"schema": {...json-schema...}, "output_mapping": {...}}
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
