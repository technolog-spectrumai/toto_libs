import cv2
from toto.executor import RestrictedPythonExecutor
from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError
import numpy as np
import networkx as nx
import io
import json


class LambdaLayer(models.Model):
    """
    Layer definition: declares which external dependencies are required.
    Standalone, reusable across workflows.
    """

    name = models.CharField(max_length=100, unique=True)

    # Dependency flags
    use_opencv = models.BooleanField(default=False)
    use_numpy = models.BooleanField(default=False)
    use_networkx = models.BooleanField(default=False)
    use_io = models.BooleanField(default=False)
    use_json = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    def render_dependencies(self) -> dict:
        """
        Return a dict of allowed globals based on enabled flags.
        """
        deps = {}

        if self.use_opencv:
            deps["cv2"] = cv2

        if self.use_numpy:
            deps["np"] = np

        if self.use_networkx:
            deps["nx"] = nx

        if self.use_io:
            deps["io"] = io

        if self.use_json:
            deps["json"] = json

        return deps



class LambdaNode(models.Model):
    layer = models.ForeignKey("LambdaLayer", on_delete=models.SET_NULL, null=True, blank=True, related_name="nodes")
    name = models.CharField(max_length=100)
    code = models.TextField(help_text="Restricted Python code snippet")

    def __str__(self):
        return f"Lambda: {self.name}"

    def execute(self, context: dict = None, extra_dependencies: dict = None):
        """
        Execute restricted Python code using the helper executor.
        Allows injecting extra dependencies (e.g., widget classes).
        """

        # Base deps from layer
        deps = self.layer.render_dependencies() if self.layer else {}

        # Merge extra dependencies
        if extra_dependencies:
            deps.update(extra_dependencies)

        executor = RestrictedPythonExecutor(
            code=self.code,
            dependencies=deps,
            name=getattr(self, "name", "unnamed")
        )

        return executor.execute(context)

    def compile(self):
        executor = RestrictedPythonExecutor(
            code=self.code,
            name=self.name,
            dependencies=self.layer.render_dependencies() if self.layer else {}
        )
        return executor.compile()


class LambdaUnitTest(models.Model):
    """
    Stores a unit test for a LambdaNode.
    Executes the node with given input and compares with expected output.
    """

    node = models.ForeignKey(
        "LambdaNode",
        on_delete=models.CASCADE,
        related_name="unit_tests"
    )

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    # JSON input passed to LambdaNode.execute()
    input_context = models.JSONField(default=dict)

    # Expected result from the node
    expected_output = models.JSONField(default=dict)

    # Execution results
    actual_output = models.JSONField(default=dict, blank=True)
    passed = models.BooleanField(default=False)
    executed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("node", "name")

    def __str__(self):
        return f"Test {self.name} for {self.node.name}"

    def run(self):
        """
        Execute the LambdaNode and compare output.
        Saves results to the database.
        """
        if not self.node:
            raise ValidationError("Unit test must be attached to a LambdaNode.")

        # Run the node
        output = self.node.execute(self.input_context)

        # Store results
        self.actual_output = output
        self.passed = (output == self.expected_output)
        self.executed_at = timezone.now()
        self.save()

        return {
            "passed": self.passed,
            "expected": self.expected_output,
            "actual": self.actual_output,
        }

