"""Tests for the ingestor strategy pattern (registry + the three strategies)."""
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from toto.ingestor.models import IngestProposal
from toto.ingestor.services.strategies import IngestStrategy
from toto.ingestor.services.strategies import llm as llm_mod


class RegistryTests(SimpleTestCase):
    def test_registry_has_three_strategies(self):
        self.assertEqual(set(IngestStrategy.registry), {"deterministic", "llm", "kg-builder"})

    def test_choices_shape_and_modes(self):
        by_key = {c["key"]: c for c in IngestStrategy.choices()}
        self.assertEqual(by_key["deterministic"]["mode"], "review")
        self.assertEqual(by_key["llm"]["mode"], "review")
        self.assertEqual(by_key["kg-builder"]["mode"], "autobuild")


class AvailabilityTests(SimpleTestCase):
    """Strategies are gated on their backend being configured (see is_available)."""

    @override_settings(OPENAI_API_KEY="")
    def test_deterministic_always_available(self):
        self.assertTrue(IngestStrategy.get("deterministic").is_available())

    @override_settings(OPENAI_API_KEY="")
    def test_llm_hidden_without_openai_key(self):
        self.assertFalse(IngestStrategy.get("llm").is_available())

    @override_settings(OPENAI_API_KEY="sk-x")
    def test_llm_available_with_openai_key(self):
        self.assertTrue(IngestStrategy.get("llm").is_available())

    @override_settings(OPENAI_API_KEY="sk-x", VICUNA_EMBEDDINGS_ENABLED=False)
    def test_kg_builder_hidden_without_vicuna_embeddings(self):
        self.assertFalse(IngestStrategy.get("kg-builder").is_available())

    @override_settings(OPENAI_API_KEY="", VICUNA_EMBEDDINGS_ENABLED=True)
    def test_kg_builder_hidden_without_openai_key(self):
        self.assertFalse(IngestStrategy.get("kg-builder").is_available())

    @override_settings(OPENAI_API_KEY="sk-x", VICUNA_EMBEDDINGS_ENABLED=True)
    def test_kg_builder_available_with_key_and_embeddings(self):
        self.assertTrue(IngestStrategy.get("kg-builder").is_available())

    def test_choices_unfiltered_returns_all(self):
        self.assertEqual(
            {c["key"] for c in IngestStrategy.choices()},
            {"deterministic", "llm", "kg-builder"},
        )

    @override_settings(OPENAI_API_KEY="", VICUNA_EMBEDDINGS_ENABLED=False)
    def test_choices_available_only_filters_to_backend_present(self):
        self.assertEqual(
            {c["key"] for c in IngestStrategy.choices(available_only=True)},
            {"deterministic"},
        )


class LLMNormalizeTests(SimpleTestCase):
    def test_drops_unknown_slugs_and_builds_contract(self):
        cats = {"person": "Person"}
        edges = {"knows": {"name": "Knows", "rel_type": "KNOWS", "sources": ["person"], "targets": ["person"]}}
        raw = {
            "nodes": [
                {"temp_id": "n1", "category_slug": "person", "display": "Ada", "properties": {"name": "Ada"}},
                {"temp_id": "n2", "category_slug": "bogus", "display": "X"},  # dropped
            ],
            "relationships": [
                {"temp_id": "r1", "edge_type_slug": "knows", "from": "n1", "to": "n2"},  # to dropped → dropped
                {"temp_id": "r2", "edge_type_slug": "nope", "from": "n1", "to": "n1"},  # unknown slug → dropped
            ],
        }
        out = llm_mod._normalize(raw, cats, edges)
        self.assertEqual([n["temp_id"] for n in out["nodes"]], ["n1"])
        self.assertEqual(out["nodes"][0]["category_name"], "Person")
        self.assertEqual(out["nodes"][0]["approval"], "pending")
        self.assertEqual(out["nodes"][0]["kind"], "new")
        self.assertEqual(out["relationships"], [])


class LLMExistingNodesTests(SimpleTestCase):
    def test_prompt_lists_existing_nodes(self):
        p = llm_mod._prompt(
            "txt", {"organization": "Org"}, {},
            existing_nodes=[{"uid": "u1", "display": "Acme", "category_slug": "organization"}],
        )
        self.assertIn("existing_uid=u1", p)
        self.assertIn("Acme", p)

    def test_prompt_omits_section_without_existing_nodes(self):
        p = llm_mod._prompt("txt", {"person": "Person"}, {})
        self.assertNotIn("existing_uid=", p)

    def test_normalize_maps_existing_uid_to_existing_node(self):
        raw = {
            "nodes": [
                {"temp_id": "n1", "existing_uid": "u1"},
                {"temp_id": "n2", "category_slug": "person", "display": "Ada", "properties": {"name": "Ada"}},
            ],
            "relationships": [{"temp_id": "r1", "edge_type_slug": "works-at", "from": "n2", "to": "n1"}],
        }
        existing_by_uid = {"u1": {"uid": "u1", "display": "Acme", "category_slug": "organization"}}
        edges = {"works-at": {"name": "Works at", "rel_type": "WORKS_AT",
                              "sources": ["person"], "targets": ["organization"]}}
        out = llm_mod._normalize(raw, {"person": "Person", "organization": "Org"}, edges, existing_by_uid)
        by_temp = {n["temp_id"]: n for n in out["nodes"]}
        self.assertEqual(by_temp["n1"]["kind"], "existing")
        self.assertEqual(by_temp["n1"]["uid"], "u1")
        self.assertEqual(by_temp["n1"]["category_slug"], "organization")
        self.assertEqual(by_temp["n2"]["kind"], "new")
        self.assertEqual(len(out["relationships"]), 1)  # both endpoints valid

    def test_normalize_unknown_existing_uid_falls_back_to_new(self):
        raw = {"nodes": [{"temp_id": "n1", "existing_uid": "bogus", "category_slug": "person",
                          "display": "X", "properties": {"name": "X"}}], "relationships": []}
        existing_by_uid = {"u1": {"uid": "u1", "display": "A", "category_slug": "person"}}
        out = llm_mod._normalize(raw, {"person": "Person"}, {}, existing_by_uid)
        self.assertEqual(out["nodes"][0]["kind"], "new")


class LLMSearchablePropertyTests(SimpleTestCase):
    def test_fills_category_identifier_property(self):
        # 'kanban-doc-section' requires 'title'; the LLM returned only a display →
        # the identifier property is filled so the required field isn't left empty.
        raw = {"nodes": [{"temp_id": "n1", "category_slug": "kanban-doc-section",
                          "display": "Computer vision"}], "relationships": []}
        out = llm_mod._normalize(
            raw, {"kanban-doc-section": "Kanban Doc Section"}, {}, None,
            {"kanban-doc-section": "title"},
        )
        node = out["nodes"][0]
        self.assertEqual(node["kind"], "new")
        self.assertEqual(node["properties"]["title"], "Computer vision")
        self.assertNotIn("name", node["properties"])  # no buried 'name'


class LLMBentoSchemaTests(TestCase):
    def test_excludes_internal_and_maps_searchable(self):
        from toto.bento.models import BentoCategory

        BentoCategory.objects.create(
            name="Concept", slug="concept", neo4j_label="Concept",
            property_schema=[{"name": "name", "type": "string", "required": True}],
        )
        BentoCategory.objects.create(
            name="Kanban Doc Section", slug="kanban-doc-section", neo4j_label="KanbanDocSection",
            internal=True, property_schema=[{"name": "title", "type": "string", "required": True}],
        )
        categories, _edges, searchable = llm_mod._bento_schema()
        self.assertIn("concept", categories)
        self.assertNotIn("kanban-doc-section", categories)  # internal hidden from the LLM
        self.assertEqual(searchable["concept"], "name")


class DeterministicRunTests(TestCase):
    def test_persists_review_proposal(self):
        with mock.patch(
            "toto.ingestor.services.strategies.deterministic.build_proposal_dict",
            return_value=({"nodes": [], "relationships": []}, {"total_changes": 0}),
        ):
            obj = IngestStrategy.get("deterministic").run("hi", user=None)
        self.assertEqual(obj.status, IngestProposal.STATUS_READY)


class LLMRunTests(TestCase):
    @override_settings(OPENAI_API_KEY="sk-x")
    def test_persists_review_proposal(self):
        from toto.bento.models import BentoCategory

        BentoCategory.objects.create(name="Person", slug="person", neo4j_label="Person")
        client = mock.Mock()
        content = '{"nodes":[{"temp_id":"n1","category_slug":"person","display":"Ada","properties":{"name":"Ada"}}],"relationships":[]}'
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )
        with mock.patch("openai.OpenAI", return_value=client), \
             mock.patch("toto.ingestor.services.validation.revalidate"), \
             mock.patch("toto.ingestor.services.validation.summarize", return_value={"total_changes": 1}):
            obj = IngestStrategy.get("llm").run("Ada is a person", user=None)
        self.assertEqual(obj.status, IngestProposal.STATUS_READY)
        self.assertEqual(obj.proposal["nodes"][0]["category_slug"], "person")

    @override_settings(OPENAI_API_KEY="")
    def test_requires_key(self):
        with self.assertRaises(RuntimeError):
            IngestStrategy.get("llm").run("x", user=None)


class KGBuilderRunTests(TestCase):
    @override_settings(OPENAI_API_KEY="sk-x")
    def test_autobuild_persists_applied(self):
        fake_pipe = mock.Mock()

        async def _run_async(**kwargs):
            return SimpleNamespace(result="ok")

        fake_pipe.run_async = _run_async
        with mock.patch("neo4j_graphrag.experimental.pipeline.kg_builder.SimpleKGPipeline", return_value=fake_pipe), \
             mock.patch("neo4j_graphrag.llm.OpenAILLM"), \
             mock.patch("toto.ravioli.connection.Neo4jClient") as Client, \
             mock.patch("toto.ravioli.rag.VicunaEmbedder"):
            obj = IngestStrategy.get("kg-builder").run("Ada knows Bob", user=None)
        self.assertEqual(obj.status, IngestProposal.STATUS_APPLIED)
        Client.return_value.close.assert_called_once()  # driver always closed

    @override_settings(OPENAI_API_KEY="")
    def test_requires_key(self):
        with self.assertRaises(RuntimeError):
            IngestStrategy.get("kg-builder").run("x", user=None)

    @override_settings(OPENAI_API_KEY="sk-x")
    def test_include_existing_prepends_known_entities_to_text(self):
        captured = {}

        async def _run_async(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(result="ok")

        fake_pipe = mock.Mock()
        fake_pipe.run_async = _run_async
        with mock.patch("neo4j_graphrag.experimental.pipeline.kg_builder.SimpleKGPipeline", return_value=fake_pipe), \
             mock.patch("neo4j_graphrag.llm.OpenAILLM"), \
             mock.patch("toto.ravioli.connection.Neo4jClient"), \
             mock.patch("toto.ravioli.rag.VicunaEmbedder"), \
             mock.patch("toto.ingestor.services.strategies.kg_builder.existing_nodes_in_text",
                        return_value=[{"uid": "u1", "display": "Acme", "category_slug": "organization"}]):
            IngestStrategy.get("kg-builder").run("Ada knows Acme", user=None, include_existing_nodes=True)
        self.assertIn("Known existing entities", captured["text"])
        self.assertIn("Acme", captured["text"])
