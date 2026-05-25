"""
Contracts app tests.
"""
import glob
import json
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import (
    SUPPORTED_EDGE_TYPES,
    SUPPORTED_NODE_TYPES,
    Contract,
    ContractEdge,
    ContractNode,
)
from .services import (
    contract_to_cytoscape,
    create_edge,
    create_manual_node,
    create_node,
)

User = get_user_model()
_SIMPLE_STATIC = "django.contrib.staticfiles.storage.StaticFilesStorage"

REMOVED_NODE_TYPES = [
    "financial_instrument", "payment", "record", "state", "wait", "approval",
    "split", "escrow", "release", "refund", "vesting", "amortize", "stake",
    "start", "end", "stop", "virtual",
]
REMOVED_EDGE_TYPES = ["pays", "owes", "scheduled_by", "next", "then", "else"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_contract(name="Test Contract"):
    return Contract.objects.create(name=name, description="A test contract.")


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

class ContractModelTests(TestCase):
    def test_create_with_description(self):
        c = _make_contract()
        self.assertEqual(c.description, "A test contract.")
        self.assertIsNotNone(c.uuid)

    def test_str(self):
        c = _make_contract("Alpha")
        self.assertEqual(str(c), "Alpha")

    def test_unique_name(self):
        _make_contract("Unique")
        with self.assertRaises(Exception):
            _make_contract("Unique")


class ContractNodeModelTests(TestCase):
    def setUp(self):
        self.contract = _make_contract()

    def test_manual_node_no_object_ref(self):
        n = ContractNode.objects.create(
            contract=self.contract, key="note1", node_type="note",
            title="Business Note", is_manual=True,
        )
        self.assertTrue(n.is_manual)
        self.assertFalse(n.is_backed)
        self.assertIsNone(n.get_object())

    def test_unique_per_contract_key(self):
        ContractNode.objects.create(
            contract=self.contract, key="ent1", node_type="entitlement", title="E1"
        )
        with self.assertRaises(Exception):
            ContractNode.objects.create(
                contract=self.contract, key="ent1", node_type="obligation", title="E2"
            )

    def test_same_key_different_contracts(self):
        c2 = _make_contract("Contract B")
        ContractNode.objects.create(contract=self.contract, key="k1", node_type="event", title="E")
        n = ContractNode.objects.create(contract=c2, key="k1", node_type="event", title="E")
        self.assertEqual(n.contract, c2)

    def test_is_backed_true(self):
        n = ContractNode.objects.create(
            contract=self.contract, key="obl1", node_type="obligation", title="Obl",
            object_app="assets", object_model="obligation", object_id="99",
        )
        self.assertTrue(n.is_backed)

    def test_get_object_returns_none_on_missing(self):
        n = ContractNode.objects.create(
            contract=self.contract, key="obl2", node_type="obligation", title="Obl",
            object_app="assets", object_model="obligation", object_id="999999",
        )
        self.assertIsNone(n.get_object())

    def test_clean_rejects_unsupported_node_type(self):
        for bad_type in REMOVED_NODE_TYPES:
            n = ContractNode(contract=self.contract, key=f"bad_{bad_type}", node_type=bad_type, title="X")
            with self.assertRaises(ValidationError):
                n.clean()

    def test_clean_accepts_supported_node_types(self):
        for good_type in SUPPORTED_NODE_TYPES:
            n = ContractNode(contract=self.contract, key=f"good_{good_type}", node_type=good_type, title="X")
            n.clean()  # should not raise


class ContractEdgeModelTests(TestCase):
    def setUp(self):
        self.contract = _make_contract()
        self.src = ContractNode.objects.create(contract=self.contract, key="src", node_type="schedule", title="S")
        self.tgt = ContractNode.objects.create(contract=self.contract, key="tgt", node_type="obligation", title="T")

    def test_rejects_cross_contract_source(self):
        c2 = _make_contract("C2")
        wrong_src = ContractNode.objects.create(contract=c2, key="x", node_type="event", title="X")
        edge = ContractEdge(contract=self.contract, source=wrong_src, target=self.tgt, edge_type="triggers")
        with self.assertRaises(ValidationError):
            edge.clean()

    def test_rejects_cross_contract_target(self):
        c2 = _make_contract("C2b")
        wrong_tgt = ContractNode.objects.create(contract=c2, key="y", node_type="allocation", title="Y")
        edge = ContractEdge(contract=self.contract, source=self.src, target=wrong_tgt, edge_type="grants")
        with self.assertRaises(ValidationError):
            edge.clean()

    def test_rejects_unsupported_edge_type(self):
        for bad in REMOVED_EDGE_TYPES:
            edge = ContractEdge(contract=self.contract, source=self.src, target=self.tgt, edge_type=bad)
            with self.assertRaises(ValidationError):
                edge.clean()

    def test_accepts_supported_edge_types(self):
        for good in SUPPORTED_EDGE_TYPES:
            edge = ContractEdge(contract=self.contract, source=self.src, target=self.tgt, edge_type=good)
            edge.clean()  # should not raise


# ---------------------------------------------------------------------------
# Service tests
# ---------------------------------------------------------------------------

class ServiceTests(TestCase):
    def setUp(self):
        self.contract = _make_contract("Service Test")

    def test_create_node_idempotent(self):
        n1 = create_node(self.contract, "ent1", "entitlement", "Access")
        n2 = create_node(self.contract, "ent1", "entitlement", "Access Updated")
        self.assertEqual(n1.pk, n2.pk)
        self.assertEqual(ContractNode.objects.filter(contract=self.contract, key="ent1").count(), 1)
        n2.refresh_from_db()
        self.assertEqual(n2.title, "Access Updated")

    def test_create_manual_node(self):
        n = create_manual_node(self.contract, "note1", "Business Note", node_type="note")
        self.assertTrue(n.is_manual)
        self.assertEqual(n.node_type, "note")
        self.assertFalse(n.is_backed)

    def test_create_edge_idempotent(self):
        create_node(self.contract, "a", "schedule", "A")
        create_node(self.contract, "b", "obligation", "B")
        e1 = create_edge(self.contract, "a", "b", "triggers")
        e2 = create_edge(self.contract, "a", "b", "triggers")
        self.assertEqual(e1.pk, e2.pk)

    def test_create_edge_validates_same_contract(self):
        c2 = _make_contract("Other")
        create_node(self.contract, "x", "event", "X")
        create_node(c2, "y", "allocation", "Y")
        with self.assertRaises(Exception):
            create_edge(self.contract, "x", "y", "triggers")

    def test_contract_to_cytoscape_nodes_edges(self):
        create_node(self.contract, "ent", "entitlement", "Access")
        create_node(self.contract, "obl", "obligation", "Fee")
        create_edge(self.contract, "ent", "obl", "creates_duty")
        data = contract_to_cytoscape(self.contract)
        self.assertEqual(len(data["nodes"]), 2)
        self.assertEqual(len(data["edges"]), 1)

    def test_cytoscape_marks_manual_node(self):
        create_manual_node(self.contract, "note1", "Note")
        data = contract_to_cytoscape(self.contract)
        node_data = data["nodes"][0]["data"]
        self.assertTrue(node_data["is_manual"])

    def test_cytoscape_no_color_fields(self):
        create_node(self.contract, "ent", "entitlement", "A")
        data = contract_to_cytoscape(self.contract)
        for n in data["nodes"]:
            for key in n["data"]:
                self.assertNotIn("color", key.lower(), f"Unexpected color field: {key}")

    def test_contract_node_references_another_contract(self):
        c2 = _make_contract("Referenced")
        n = create_node(self.contract, "ref_c2", "contract", "Ref to C2", object=c2)
        self.assertTrue(n.is_backed)
        self.assertEqual(n.object_app, "contracts")
        self.assertEqual(n.object_model, "contract")
        resolved = n.get_object()
        self.assertEqual(resolved, c2)


# ---------------------------------------------------------------------------
# View / UI tests
# ---------------------------------------------------------------------------

@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": _SIMPLE_STATIC},
    },
)
class ContractViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="contracts-tester", password="x")
        cls.contract = _make_contract("View Test Contract")
        cls.node = ContractNode.objects.create(
            contract=cls.contract, key="ent1", node_type="entitlement", title="Access"
        )
        cls.node2 = ContractNode.objects.create(
            contract=cls.contract, key="obl1", node_type="obligation", title="Fee"
        )
        cls.edge = ContractEdge.objects.create(
            contract=cls.contract, source=cls.node, target=cls.node2,
            edge_type="creates_duty",
        )

    def setUp(self):
        self.client.force_login(self.user)
        patcher = patch("toto.ui.page.PageProcessor._get_config", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_contract_list_renders(self):
        r = self.client.get(reverse("contracts:contract_list"))
        self.assertEqual(r.status_code, 200)

    def test_contract_list_search(self):
        r = self.client.get(reverse("contracts:contract_list"), {"q": "View Test"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "View Test Contract")

    def test_contract_detail_renders(self):
        r = self.client.get(reverse("contracts:contract_detail", args=[self.contract.uuid]))
        self.assertEqual(r.status_code, 200)

    def test_contract_detail_shows_description(self):
        r = self.client.get(reverse("contracts:contract_detail", args=[self.contract.uuid]))
        self.assertContains(r, "A test contract.")

    def test_contract_detail_contains_cytoscape_element(self):
        r = self.client.get(reverse("contracts:contract_detail", args=[self.contract.uuid]))
        self.assertContains(r, "cy-mesh")

    def test_contract_detail_contains_oya_theme_colors(self):
        r = self.client.get(reverse("contracts:contract_detail", args=[self.contract.uuid]))
        self.assertContains(r, "oya-theme-colors")

    def test_contract_detail_contains_nodes_table(self):
        r = self.client.get(reverse("contracts:contract_detail", args=[self.contract.uuid]))
        self.assertContains(r, "ent1")

    def test_contract_detail_contains_edges_table(self):
        r = self.client.get(reverse("contracts:contract_detail", args=[self.contract.uuid]))
        self.assertContains(r, "creates_duty")

    def test_contract_detail_no_yaml_section(self):
        r = self.client.get(reverse("contracts:contract_detail", args=[self.contract.uuid]))
        self.assertNotContains(r, "Contract YAML")

    def test_contract_create_get(self):
        r = self.client.get(reverse("contracts:contract_create"))
        self.assertEqual(r.status_code, 200)

    def test_contract_update_get(self):
        r = self.client.get(reverse("contracts:contract_update", args=[self.contract.uuid]))
        self.assertEqual(r.status_code, 200)

    def test_contract_graph_json(self):
        r = self.client.get(reverse("contracts:contract_graph_json", args=[self.contract.uuid]))
        self.assertEqual(r.status_code, 200)
        data = json.loads(r.content)
        self.assertIn("nodes", data)
        self.assertIn("edges", data)
        self.assertEqual(len(data["nodes"]), 2)
        self.assertEqual(len(data["edges"]), 1)

    def test_graph_json_no_color_fields(self):
        r = self.client.get(reverse("contracts:contract_graph_json", args=[self.contract.uuid]))
        data = json.loads(r.content)
        for n in data["nodes"]:
            for key in n["data"]:
                self.assertNotIn("color", key.lower())

    def test_contract_404_on_unknown_uuid(self):
        import uuid
        r = self.client.get(reverse("contracts:contract_detail", args=[uuid.uuid4()]))
        self.assertEqual(r.status_code, 404)

    def test_node_create_get(self):
        r = self.client.get(reverse("contracts:contract_node_create", args=[self.contract.uuid]))
        self.assertEqual(r.status_code, 200)

    def test_edge_create_get(self):
        r = self.client.get(reverse("contracts:contract_edge_create", args=[self.contract.uuid]))
        self.assertEqual(r.status_code, 200)


# ---------------------------------------------------------------------------
# Template hygiene tests
# ---------------------------------------------------------------------------

class ContractTemplateHygieneTests(TestCase):
    @classmethod
    def _templates(cls):
        base = os.path.join(os.path.dirname(__file__), "templates", "contracts")
        return glob.glob(os.path.join(base, "**", "*.html"), recursive=True)

    def _all_content(self):
        parts = []
        for path in self._templates():
            with open(path) as f:
                parts.append(f.read())
        return "\n".join(parts)

    def test_no_teal_wording(self):
        self.assertNotIn("TEAL", self._all_content())

    def test_no_ast_wording(self):
        import re
        self.assertNotRegex(self._all_content(), r"\bAST\b")

    def test_no_dry_run_wording(self):
        self.assertNotIn("dry-run", self._all_content().lower())
        self.assertNotIn("dry run", self._all_content().lower())

    def test_no_deploy_contract_button(self):
        self.assertNotIn("deploy contract", self._all_content().lower())
        self.assertNotIn("test contract", self._all_content().lower())

    def test_no_high_level_instrument_concepts_in_legend(self):
        content = self._all_content().lower()
        for bad in ["escrow", "vesting", "staking", "amortiz", "forward contract"]:
            self.assertNotIn(bad, content, f"Found disallowed concept: {bad}")

    def test_graph_card_includes_oya_theme_colors(self):
        graph_card = os.path.join(os.path.dirname(__file__), "templates", "contracts", "_graph_card.html")
        with open(graph_card) as f:
            content = f.read()
        self.assertIn("oya-theme-colors", content)

    def test_graph_card_includes_cy_mesh_container(self):
        graph_card = os.path.join(os.path.dirname(__file__), "templates", "contracts", "_graph_card.html")
        with open(graph_card) as f:
            content = f.read()
        self.assertIn("cy-mesh", content)
