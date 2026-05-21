import tempfile
from pathlib import Path

from django.test import TestCase, override_settings

from toto.api.models import Connector as ApiConnector
from toto.core.connectors import list_connector_types, validate_connector_type
from toto.mandragora.management.commands.ingress_mandragora import Command
from toto.workflows.models import WorkflowConnector


class MandragoraIngressTests(TestCase):
    def test_seed_connectors_creates_all_registered_connector_types(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(WORKFLOW_FILE_CONNECTOR_ROOT=tmpdir):
                Command()._seed_connectors()

            registered_types = {item["connector_type"] for item in list_connector_types()}
            seeded_types = set(WorkflowConnector.objects.values_list("connector_type", flat=True))

            self.assertTrue(registered_types.issubset(seeded_types))
            self.assertTrue((Path(tmpdir) / "ingress" / "sample-input.json").exists())
            self.assertTrue(ApiConnector.objects.filter(slug="httpbin-demo").exists())

            for connector in WorkflowConnector.objects.all():
                self.assertEqual(
                    validate_connector_type(connector.connector_type, connector.config),
                    [],
                )
