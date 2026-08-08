"""Workflow DAG validator tests — against the real raises-API.

(The previous version of this module called a list-returning
``WorkflowValidator(wf).validate()`` that never existed in this tree; it ran
in no gate, which is why it never failed. Listed explicitly in the gate now —
``toto`` is a namespace package, discovery finds nothing by itself.)
"""

from django.test import TestCase

from .models import LambdaFunction, Workflow, WorkflowNode
from .services.validator import ValidationError, WorkflowValidator


def _lambda(name="fn") -> LambdaFunction:
    return LambdaFunction.objects.create(function_name=name, content="pass")


class WorkflowValidatorTests(TestCase):
    def _workflow(self):
        return Workflow.objects.create(name="test")

    def test_lambda_node_requires_lambda_function(self):
        wf = self._workflow()
        WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA,
                                    label="l")
        with self.assertRaises(ValidationError) as ctx:
            WorkflowValidator().validate(wf)
        self.assertTrue(any("lambda" in e.lower() for e in ctx.exception.errors))

    def test_valid_lambda_node(self):
        wf = self._workflow()
        WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA,
                                    label="l", lambda_function=_lambda())
        WorkflowValidator().validate(wf)  # must not raise

    def test_empty_workflow_is_invalid(self):
        with self.assertRaises(ValidationError):
            WorkflowValidator().validate(self._workflow())
