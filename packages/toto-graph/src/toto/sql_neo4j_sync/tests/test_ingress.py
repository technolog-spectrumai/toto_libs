from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from toto.ravioli import neojson


class IngressSampleNeoJsonTests(TestCase):
    """ingress_sql_neo4j_sync seeds a sample .neojson file idempotently."""

    def test_ingress_seeds_sample_file(self):
        from toto.vault.models import Bucket, VaultFile

        owner = User.objects.create_superuser("ingress_super", password="pw")
        Bucket.objects.create(name="General", owner=owner, slug="general")

        call_command("ingress_sql_neo4j_sync", stdout=StringIO(), stderr=StringIO())
        qs = VaultFile.objects.filter(file_type="neojson", key="sample-graph-neojson")
        self.assertEqual(qs.count(), 1)

        # Idempotent: a second run does not create a duplicate.
        call_command("ingress_sql_neo4j_sync", stdout=StringIO(), stderr=StringIO())
        self.assertEqual(qs.count(), 1)

        with qs.first().file.open("rb") as fh:
            graph = neojson.loads(fh.read().decode("utf-8"))
        self.assertEqual(neojson.validate(graph), [])
