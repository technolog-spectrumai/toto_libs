from django.db import models
from django.utils.translation import gettext_lazy as _
import cv2
from toto.models import BaseExecutableModel


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


    def __str__(self):
        return self.name

    def render_dependencies(self) -> dict:
        """
        Return a dict of allowed globals based on enabled flags.
        """
        deps = {}
        if self.use_opencv:

            deps["cv2"] = cv2
        return deps


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
        # start with the base defaults
        globals_dict = BaseExecutableModel.get_default_allowed_globals()
        # add the runtime context
        globals_dict["context"] = context

        # merge in layer dependencies if present
        if self.layer:
            globals_dict.update(self.layer.render_dependencies())

        return globals_dict


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
