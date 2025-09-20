from django.db import models
from django.utils.safestring import mark_safe
from RestrictedPython import compile_restricted, safe_globals


class Workflow(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    def mermaid_dag(self):
        lines = ["graph TD"]
        for edge in self.edges.select_related("source", "target").all():
            src = edge.source.name.replace(" ", "_")
            tgt = edge.target.name.replace(" ", "_")
            lines.append(f"{src}[\"{edge.source.name}\"] --> {tgt}[\"{edge.target.name}\"]")
        diagram = "\n".join(lines)
        html = f"""
        <div class="mermaid">
        {diagram}
        </div>
        <script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
        <script>mermaid.initialize({{ startOnLoad: true }});</script>
        """
        return mark_safe(html)


class Node(models.Model):
    workflow = models.ForeignKey(Workflow, related_name="nodes", on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    # Retry settings
    countdown = models.PositiveIntegerField(default=10, help_text="Seconds to wait before retrying")
    max_retries = models.PositiveIntegerField(default=3, help_text="Maximum number of retries")

    class Meta:
        abstract = False

    def __str__(self):
        return self.name

    def execute(self, input_data):
        raise NotImplementedError("Subclasses must implement execute()")

    def get_subclass(self):
        for subclass in self.__class__.__subclasses__():
            try:
                return subclass.objects.get(pk=self.pk)
            except subclass.DoesNotExist:
                continue
        return self


class FunctionNode(Node):
    code = models.TextField(help_text="Define a Python function named `run(input_data)`")

    def execute(self, input_data):
        compiled = compile_restricted(self.code, filename="<function>", mode="exec")
        env = safe_globals.copy()
        env["input_data"] = input_data
        exec(compiled, env)
        return env["run"](input_data)


class AlgoNode(Node):
    ALGORITHM_CHOICES = [
        ("algo1", "Algorithm 1"),
        ("algo2", "Algorithm 2"),
        ("algo3", "Algorithm 3"),
    ]

    algorithm = models.CharField(
        max_length=20,
        choices=ALGORITHM_CHOICES,
        default="algo1",
        help_text="Select which algorithm to run"
    )

    def execute(self, input_data):
        if self.algorithm == "algo1":
            return self.run_algo1(input_data)
        elif self.algorithm == "algo2":
            return self.run_algo2(input_data)
        elif self.algorithm == "algo3":
            return self.run_algo3(input_data)
        else:
            return {"error": f"Unknown algorithm: {self.algorithm}"}

    def run_algo1(self, input_data):
        return {"result": f"Algo1 processed {input_data}"}

    def run_algo2(self, input_data):
        return {"result": f"Algo2 transformed {input_data}"}

    def run_algo3(self, input_data):
        return {"result": f"Algo3 analyzed {input_data}"}


class Edge(models.Model):
    workflow = models.ForeignKey(Workflow, related_name="edges", on_delete=models.CASCADE)
    source = models.ForeignKey(Node, related_name="outgoing", on_delete=models.CASCADE)
    target = models.ForeignKey(Node, related_name="incoming", on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.source.name} → {self.target.name}"

STATUS_CHOICES = [
    ("pending", "Pending"),
    ("running", "Running"),
    ("success", "Success"),
    ("failed", "Failed"),
]


class WorkflowRun(models.Model):
    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending"
    )
    error = models.TextField(blank=True)

    def __str__(self):
        return f"Run of {self.workflow.name} at {self.started_at}"


class NodeRun(models.Model):
    workflow_run = models.ForeignKey(WorkflowRun, related_name="node_runs", on_delete=models.CASCADE)
    node = models.ForeignKey(Node, on_delete=models.CASCADE)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending"
    )
    input_data = models.JSONField(default=dict)
    output_data = models.JSONField(default=dict)
    error = models.TextField(blank=True)
    task_id = models.CharField(max_length=255, null=True, blank=True)
    terminated = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.node.name} in run {self.workflow_run.id}"

