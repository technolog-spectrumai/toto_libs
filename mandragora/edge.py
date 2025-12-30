# from copy import deepcopy
# from django.utils.translation import gettext_lazy as _
# import cv2
# from toto.executor import RestrictedPythonExecutor
# from django.db import models
# from django.utils import timezone
# from django.core.exceptions import ValidationError
#
#
# class Workflow(models.Model):
#     """
#     Workflow definition: a chain of Lambda nodes.
#     Identified only by primary key or name.
#     """
#     name = models.CharField(max_length=100, unique=True)
#     description = models.TextField(blank=True)
#     timeout = models.PositiveIntegerField(
#         default=30,
#         help_text=_("Timeout in seconds for workflow execution")
#     )
#
#     def __str__(self):
#         return self.name
#
#     def run(self, context=None):
#         result = {}
#         node = self.nodes.filter(is_initial=True).first()
#         context = context or {}
#
#         while node:
#             result = node.execute(context)
#             context = deepcopy(result)
#
#             if node.is_final:
#                 break
#
#             next_edge = node.outgoing_edges.first()
#             if next_edge:
#                 node = next_edge.target
#             else:
#                 break
#
#         return result
#
#
# class LambdaLayer(models.Model):
#     """
#     Layer definition: declares which external dependencies are required.
#     Standalone, reusable across workflows.
#     """
#     name = models.CharField(max_length=100, unique=True)
#
#     # Boolean flags for important dependencies
#     use_opencv = models.BooleanField(default=False)
#
#
#     def __str__(self):
#         return self.name
#
#     def render_dependencies(self) -> dict:
#         """
#         Return a dict of allowed globals based on enabled flags.
#         """
#         deps = {}
#         if self.use_opencv:
#
#             deps["cv2"] = cv2
#         return deps
#
#
# class LambdaNode(models.Model):
#     workflow = models.ForeignKey("Workflow", on_delete=models.CASCADE, related_name="nodes")
#     layer = models.ForeignKey("LambdaLayer", on_delete=models.SET_NULL, null=True, blank=True, related_name="nodes")
#     name = models.CharField(max_length=100)
#     code = models.TextField(help_text="Restricted Python code snippet")
#
#     def __str__(self):
#         return f"{self.workflow.name}: {self.name}"
#
#     def execute(self, context: dict = None):
#         """
#         Execute restricted Python code using the helper executor.
#         """
#
#         executor = RestrictedPythonExecutor(
#             code=self.code,
#             dependencies=self.layer.render_dependencies(),
#             name=getattr(self, "name", "unnamed")
#         )
#
#         return executor.execute(context)
#
#
# class Edge(models.Model):
#     """
#     Transition between Lambda nodes.
#     """
#     workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="edges")
#     source = models.ForeignKey(LambdaNode, on_delete=models.CASCADE, related_name="outgoing_edges")
#     target = models.ForeignKey(LambdaNode, on_delete=models.CASCADE, related_name="incoming_edges")
#     action = models.CharField(max_length=100, help_text=_("Trigger name for this transition"))
#
#     class Meta:
#         unique_together = ("workflow", "source", "target", "action")
#
#     def __str__(self):
#         return f"{self.source.name} --[{self.action}]--> {self.target.name}"
#
#
# class LambdaUnitTest(models.Model):
#     """
#     Stores a unit test for a LambdaNode.
#     Executes the node with given input and compares with expected output.
#     """
#
#     node = models.ForeignKey(
#         "LambdaNode",
#         on_delete=models.CASCADE,
#         related_name="unit_tests"
#     )
#
#     name = models.CharField(max_length=100)
#     description = models.TextField(blank=True)
#
#     # JSON input passed to LambdaNode.execute()
#     input_context = models.JSONField(default=dict)
#
#     # Expected result from the node
#     expected_output = models.JSONField(default=dict)
#
#     # Execution results
#     actual_output = models.JSONField(default=dict, blank=True)
#     passed = models.BooleanField(default=False)
#     executed_at = models.DateTimeField(null=True, blank=True)
#
#     class Meta:
#         unique_together = ("node", "name")
#
#     def __str__(self):
#         return f"Test {self.name} for {self.node.name}"
#
#     def run(self):
#         """
#         Execute the LambdaNode and compare output.
#         Saves results to the database.
#         """
#         if not self.node:
#             raise ValidationError("Unit test must be attached to a LambdaNode.")
#
#         # Run the node
#         output = self.node.execute(self.input_context)
#
#         # Store results
#         self.actual_output = output
#         self.passed = (output == self.expected_output)
#         self.executed_at = timezone.now()
#         self.save()
#
#         return {
#             "passed": self.passed,
#             "expected": self.expected_output,
#             "actual": self.actual_output,
#         }
#
