"""Tests for the ravioli_reindex_embeddings command (Neo4j + embeddings mocked)."""
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings


@override_settings(
    VICUNA_EMBEDDINGS_ENABLED=True,
    TOTO_VECTOR_NODE_LABEL="TotoChunk",
    TOTO_VECTOR_TEXT_PROPERTY="text",
    TOTO_VECTOR_EMBEDDING_PROPERTY="embedding",
    TOTO_NEO4J_VECTOR_INDEX="toto_chunk_embeddings",
)
class ReindexCommandTests(SimpleTestCase):
    def test_drop_create_and_embed(self):
        client = mock.Mock()

        def run_cypher(query, params=None):
            if "RETURN elementId" in query:
                return [{"id": "e1", "text": "alpha"}, {"id": "e2", "text": "beta"}]
            return []

        client.run_cypher.side_effect = run_cypher

        with mock.patch("toto.ravioli.connection.Neo4jClient", return_value=client), \
             mock.patch("toto.vicuna.embeddings.embedding_dimension", return_value=3), \
             mock.patch("toto.vicuna.embeddings.embed_texts", return_value=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]):
            call_command("ravioli_reindex_embeddings", "--drop", "--batch", "10")

        queries = " || ".join(c.args[0] for c in client.run_cypher.call_args_list)
        self.assertIn("DROP INDEX toto_chunk_embeddings", queries)
        self.assertIn("CREATE VECTOR INDEX toto_chunk_embeddings", queries)
        self.assertIn("`vector.dimensions`: 3", queries)
        self.assertIn("SET n.embedding = row.vec", queries)
        client.close.assert_called_once()
