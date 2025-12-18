#import cv2
import json
from datetime import timedelta

from django.db import models
from django.utils.translation import gettext_lazy as _
from RestrictedPython import compile_restricted, safe_builtins, utility_builtins, limited_builtins
from django_jsonform.models.fields import JSONField  # use JSONField with schema support

# Your custom safe clients
# from storage.client import StorageClient
# from data.client import DataClient
import operator
from RestrictedPython.Guards import guarded_unpack_sequence, full_write_guard


class Workflow(models.Model):
    """
    Workflow definition: a chain of Lambda nodes.
    Identified only by primary key or name.
    """
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    timeout = models.PositiveIntegerField(
        default=30,
        help_text=_("Timeout in seconds for workflow execution")
    )
    metadata = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return self.name


class LambdaLayer(models.Model):
    """
    Layer definition: declares which external dependencies are required.
    Standalone, reusable across workflows.
    """
    name = models.CharField(max_length=100, unique=True)

    # Boolean flags for important dependencies
    use_opencv = models.BooleanField(default=False)
    use_kanban_client = models.BooleanField(default=False)


    def __str__(self):
        return self.name

    def render_dependencies(self) -> dict:
        """
        Return a dict of allowed globals based on enabled flags.
        """
        deps = {}
        if self.use_opencv:
            import cv2
            deps["cv2"] = cv2
        if self.use_kanban_client:
            from kanban.client import KanbanClient
            deps["KanbanClient"] = KanbanClient
        return deps


class BaseExecutableModel(models.Model):
    """
    Abstract base model for any node that executes restricted Python code.
    Provides `code` and `test_context` fields plus an `execute` method.
    Delegates allowed_globals construction to child classes.
    """

    code = models.TextField(help_text="Restricted Python code snippet")
    test_context = models.JSONField(blank=True, null=True, help_text="Optional JSON context for testing")

    class Meta:
        abstract = True

    def get_context(self, context: dict = None) -> dict:
        """
        Default context resolution. Subclasses can override if needed.
        """
        return context or self.test_context or {}

    def get_allowed_globals(self, context: dict) -> dict:
        """
        Subclasses must override this to provide their own allowed globals.
        """
        raise NotImplementedError("Child class must implement get_allowed_globals()")

    def execute(self, context: dict = None):
        """
        Execute restricted Python code. Requires a main(context) function.
        """
        ctx = self.get_context(context)

        try:
            byte_code = compile_restricted(
                self.code,
                filename=f"<lambda:{getattr(self, 'name', 'unnamed')}>",
                mode="exec"
            )

            allowed_globals = self.get_allowed_globals(ctx)
            local_vars = {}
            exec(byte_code, allowed_globals, local_vars)

            if "main" not in local_vars or not callable(local_vars["main"]):
                return {"error": "No main() function defined in node code"}

            return local_vars["main"](ctx)

        except Exception as e:
            return {"error": str(e)}



class LambdaNode(BaseExecutableModel):
    workflow = models.ForeignKey("Workflow", on_delete=models.CASCADE, related_name="nodes")
    layer = models.ForeignKey("LambdaLayer", on_delete=models.SET_NULL, null=True, blank=True, related_name="nodes")
    name = models.CharField(max_length=100)
    is_initial = models.BooleanField(default=False)
    is_final = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.workflow.name}: {self.name}"

    def __str__(self):
        return f"{self.workflow.name}: {self.name}"

    def get_allowed_globals(self, context: dict) -> dict:
        allowed_builtins = {}
        allowed_builtins.update(safe_builtins)
        allowed_builtins.update(utility_builtins)
        allowed_builtins.update(limited_builtins)

        return {
            "__builtins__": allowed_builtins,
            "context": context,
            "_getitem_": operator.getitem,
            "_setitem_": operator.setitem,
            "_delitem_": operator.delitem,
            "_unpack_sequence_": guarded_unpack_sequence,
            "_getiter_": iter,
            "timedelta": timedelta,
            "sum": sum,
            "len": len,
            "max": max,
            "min": min,
            "_write_": full_write_guard,
        }


class Edge(models.Model):
    """
    Transition between Lambda nodes.
    """
    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="edges")
    source = models.ForeignKey(LambdaNode, on_delete=models.CASCADE, related_name="outgoing_edges")
    target = models.ForeignKey(LambdaNode, on_delete=models.CASCADE, related_name="incoming_edges")
    action = models.CharField(max_length=100, help_text=_("Trigger name for this transition"))

    class Meta:
        unique_together = ("workflow", "source", "target", "action")

    def __str__(self):
        return f"{self.source.name} --[{self.action}]--> {self.target.name}"
