"""
Workflow DAG tests.
"""

from django.test import TestCase

from .models import LambdaFunction, Workflow, WorkflowNode, WorkflowRun
from .services.validator import WorkflowValidator


def _lambda(name="fn") -> LambdaFunction:
    return LambdaFunction.objects.create(function_name=name, content="pass")


class WorkflowValidatorTests(TestCase):
    def _workflow(self):
        wf = Workflow.objects.create(name="test")
        return wf

    def test_lambda_node_requires_lambda_function(self):
        wf = self._workflow()
        node = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="l")
        v = WorkflowValidator(wf)
        errors = v.validate()
        self.assertTrue(any("lambda" in e.lower() for e in errors))

    def test_valid_lambda_node(self):
        wf = self._workflow()
        fn = _lambda()
        WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="l", lambda_function=fn)
        v = WorkflowValidator(wf)
        errors = v.validate()
        self.assertEqual(errors, [])
