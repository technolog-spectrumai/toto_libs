from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from .services import tools as steven_tools
from .services.graph_rag import (
    GraphNodeHit,
    GraphRagRetriever,
    GraphRelationshipHit,
    GraphRagResult,
    format_graph_context,
    tokenize_query,
)


class EmptyToolManager:
    def filter(self, **kwargs):
        return self

    def values_list(self, *args, **kwargs):
        return []


class FakeNeo4jClient:
    def __init__(self):
        self.calls = []

    def run_cypher(self, query, params=None):
        self.calls.append((query, params or {}))
        if "RETURN labels(n) AS labels" in query:
            return [
                {
                    "labels": ["IdeaBox"],
                    "uuid": "box-1",
                    "props": {
                        "title": "Memory palace",
                        "properties": '{"kind": "idea"}',
                        "ravioli_owned": True,
                    },
                    "matched_keys": ["title"],
                }
            ]
        if "relationships(path)" in query:
            return [
                {
                    "relation": "HAS_CATEGORY",
                    "start_labels": ["IdeaBox"],
                    "start_uuid": "box-1",
                    "end_labels": ["IdeaCategory"],
                    "end_uuid": "cat-1",
                    "props": {"ravioli_owned": True},
                }
            ]
        return []

    def close(self):
        pass


class GraphRagTests(SimpleTestCase):
    def test_tokenize_query_removes_stopwords_and_duplicates(self):
        self.assertEqual(
            tokenize_query("What is the memory memory palace status?"),
            ["memory", "palace", "status"],
        )

    def test_retriever_returns_matching_nodes_and_relationships(self):
        agent = SimpleNamespace(
            graph_rag_enabled=True,
            graph_rag_labels=["IdeaBox"],
            graph_rag_max_nodes=3,
            graph_rag_depth=1,
        )
        client = FakeNeo4jClient()
        result = GraphRagRetriever(client=client).retrieve_for_agent(
            agent,
            "memory palace",
        )

        self.assertEqual(result.terms, ["memory", "palace"])
        self.assertEqual(result.nodes[0].uuid, "box-1")
        self.assertEqual(result.nodes[0].props["title"], "Memory palace")
        self.assertEqual(result.relationships[0].relation, "HAS_CATEGORY")
        self.assertEqual(client.calls[0][1]["terms"], ["memory", "palace"])

    def test_format_graph_context_includes_nodes_and_relationships(self):
        result = GraphRagResult(
            terms=["memory"],
            nodes=[
                GraphNodeHit(
                    labels=["IdeaBox"],
                    uuid="box-1",
                    props={"title": "Memory palace"},
                    matched_keys=["title"],
                )
            ],
            relationships=[
                GraphRelationshipHit(
                    relation="HAS_CATEGORY",
                    start_labels=["IdeaBox"],
                    start_uuid="box-1",
                    end_labels=["IdeaCategory"],
                    end_uuid="cat-1",
                    props={},
                )
            ],
        )

        context = format_graph_context(result)

        self.assertIn("Graph RAG context", context)
        self.assertIn("(IdeaBox uuid=box-1)", context)
        self.assertIn("HAS_CATEGORY", context)

    def test_graph_cypher_tool_is_added_when_langchain_neo4j_is_available(self):
        class GraphRagToolManager:
            def filter(self, **kwargs):
                return self
            def values_list(self, *args, **kwargs):
                return ["graph_cypher_qa"]

        agent = SimpleNamespace(tools=GraphRagToolManager())

        with patch.object(steven_tools, "langchain_neo4j_available", return_value=True):
            tools = steven_tools.tools_for_agent(agent, llm=object())

        self.assertEqual([tool.name for tool in tools], ["graph_cypher_qa"])

    def test_graph_cypher_tool_invokes_chain(self):
        class FakeChain:
            def invoke(self, payload):
                return {"result": f"answer for {payload['query']}"}

        with patch.object(steven_tools, "build_graph_cypher_qa_chain", return_value=FakeChain()):
            graph_tool = steven_tools.graph_cypher_qa_tool(llm=object())
            answer = graph_tool.invoke({"question": "who owns memory palace?"})

        self.assertEqual(answer, "answer for who owns memory palace?")
