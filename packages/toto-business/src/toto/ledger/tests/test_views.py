"""The chain pages: the ordered read, one block, exports, and the verdict."""

from __future__ import annotations

import io
import zipfile

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from toto.core.models import Platform
from toto.ledger.models import LedgerKind
from toto.ledger.services import chain
from toto.ledger.tests.test_verify import tamper


class LedgerViewTestCase(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Test Platform", author="Tests",
                                publication_year=2026, active=True)
        self.user = get_user_model().objects.create_user("reader", password="x")
        self.ledger = chain.open_ledger(
            key="acme-actions", name="Acme actions", kind=LedgerKind.COMPANY,
            scope_type="company.company",
        )
        self.blocks = [
            chain.append(ledger=self.ledger, payload={"n": n},
                         source_type="company.action", source_ref=f"Action {n}")
            for n in (1, 2, 3)
        ]


class AccessTests(LedgerViewTestCase):
    def test_every_page_requires_login(self):
        for name, args in (
            ("ledger:index", []),
            ("ledger:detail", [self.ledger.uid]),
            ("ledger:verify", [self.ledger.uid]),
            ("ledger:export_xml", [self.ledger.uid]),
            ("ledger:export_zip", [self.ledger.uid]),
            ("ledger:graph", [self.ledger.uid]),
            ("ledger:block", [self.ledger.uid, 1]),
        ):
            with self.subTest(name=name):
                response = self.client.get(reverse(name, args=args))
                self.assertEqual(response.status_code, 302)
                self.assertIn("login", response["Location"])


class DetailTests(LedgerViewTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_it_lists_every_block_in_order_beside_the_diagram(self):
        response = self.client.get(reverse("ledger:detail", args=[self.ledger.uid]))
        self.assertContains(response, "ledger-chart")
        self.assertContains(response, "Blocks, in order")
        sequences = [entry.sequence for entry in response.context["entries"]]
        self.assertEqual(sequences, [1, 2, 3, 4])
        self.assertContains(response, "Action 1")

    def test_it_names_the_algorithm_the_chain_was_sealed_with(self):
        response = self.client.get(reverse("ledger:detail", args=[self.ledger.uid]))
        self.assertEqual(response.context["algorithm"], "sha256")
        self.assertContains(response, "sha256")

    def test_a_healthy_chain_says_so(self):
        response = self.client.get(reverse("ledger:detail", args=[self.ledger.uid]))
        self.assertTrue(response.context["verification"].ok)
        self.assertContains(response, "verify")

    def test_the_graph_json_is_one_node_per_block_linked_in_order(self):
        graph = self.client.get(
            reverse("ledger:graph", args=[self.ledger.uid])
        ).json()
        self.assertEqual(len(graph["nodes"]), 4)
        self.assertEqual(len(graph["edges"]), 3)
        self.assertEqual({node["type"] for node in graph["nodes"]}, {"ledger_entry"})
        self.assertEqual(graph["edges"][0]["source"], "block-1")
        self.assertEqual(graph["edges"][0]["target"], "block-2")


class BlockTests(LedgerViewTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_it_shows_the_exact_text_the_hash_was_taken_over(self):
        response = self.client.get(reverse("ledger:block", args=[self.ledger.uid, 2]))
        self.assertEqual(
            response.context["block_xml"], chain.entry_block_xml(self.blocks[0]),
        )
        self.assertContains(response, self.blocks[0].entry_hash)

    def test_the_genesis_block_is_labelled(self):
        response = self.client.get(reverse("ledger:block", args=[self.ledger.uid, 1]))
        self.assertContains(response, "genesis")

    def test_a_tampered_block_says_so_on_its_own_page(self):
        tamper(self.blocks[0].pk, source_ref="rewritten")
        response = self.client.get(reverse("ledger:block", args=[self.ledger.uid, 2]))
        self.assertNotEqual(
            response.context["recomputed_hash"], response.context["entry"].entry_hash,
        )
        self.assertContains(response, "does not match")

    def test_a_missing_block_is_a_404(self):
        response = self.client.get(reverse("ledger:block", args=[self.ledger.uid, 99]))
        self.assertEqual(response.status_code, 404)


class VerifyPageTests(LedgerViewTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_a_healthy_chain_reports_every_block_checked(self):
        response = self.client.get(reverse("ledger:verify", args=[self.ledger.uid]))
        self.assertTrue(response.context["result"].ok)
        self.assertEqual(response.context["result"].checked, 4)
        self.assertIsNone(response.context["failed_block"])

    def test_a_broken_chain_names_the_first_block_that_failed(self):
        tamper(self.blocks[1].pk, payload_xml="<payload><map></map></payload>")
        response = self.client.get(reverse("ledger:verify", args=[self.ledger.uid]))
        result = response.context["result"]
        self.assertFalse(result.ok)
        self.assertEqual(result.first_bad_sequence, 3)
        self.assertEqual(response.context["failed_block"].sequence, 3)
        self.assertContains(response, "does not verify")

    def test_looking_at_the_verdict_changes_nothing(self):
        before = list(
            self.ledger.entries.order_by("sequence").values_list("entry_hash", flat=True)
        )
        self.client.get(reverse("ledger:verify", args=[self.ledger.uid]))
        self.client.get(reverse("ledger:verify", args=[self.ledger.uid]))
        after = list(
            self.ledger.entries.order_by("sequence").values_list("entry_hash", flat=True)
        )
        self.assertEqual(before, after)


class ExportViewTests(LedgerViewTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_the_xml_export_downloads_as_a_named_file(self):
        response = self.client.get(reverse("ledger:export_xml", args=[self.ledger.uid]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/xml", response["Content-Type"])
        self.assertIn("ledger-acme-actions-000004.xml", response["Content-Disposition"])
        self.assertIn(b"<ledger", response.content)

    def test_the_zip_export_holds_the_manifest_and_every_block(self):
        response = self.client.get(reverse("ledger:export_zip", args=[self.ledger.uid]))
        self.assertEqual(response["Content-Type"], "application/zip")
        names = set(zipfile.ZipFile(io.BytesIO(response.content)).namelist())
        self.assertIn("manifest.xml", names)
        self.assertIn("blocks/000004.xml", names)
