"""neomodel connection URL selection.

The bug these guard against: neomodel takes a *single* Bolt URL (no multi-endpoint
fallback), so it must be pointed at a host it can actually reach. ``connection_uris``
lists the dev 127.0.0.1 loopback *first*; blindly taking it pins neomodel/bento
writes to loopback, which is empty inside a container even though NEO4J_URI points
at the in-stack ``neo4j`` service.
"""

from unittest import mock

from django.test import TestCase, override_settings

import toto.ravioli.neomodel_conn as nc
from toto.ravioli.connection import Neo4jConnectionError


def _fake_client(working_uri):
    """A Neo4jClient stand-in whose driver() 'connects' to working_uri."""
    client = mock.MagicMock()
    client._driver_uri = None

    def _driver():
        if working_uri is None:
            raise Neo4jConnectionError("nothing reachable")
        client._driver_uri = working_uri
        return mock.MagicMock()

    client.driver.side_effect = _driver
    return client


@override_settings(
    RAVIOLI_ENABLED=True,
    NEO4J_URI="bolt://neo4j:7687",
    NEO4J_USER="neo4j",
    NEO4J_PASSWORD="s3cr3t",
    RAVIOLI_NEO4J_LOCAL_FALLBACK=True,  # both 127.0.0.1 + neo4j are candidates
)
class ReachableEndpointTests(TestCase):
    def setUp(self):
        nc._configured_url = None
        nc._configured_for = None

    def test_picks_in_stack_neo4j_when_loopback_dead(self):
        # In a container: 127.0.0.1 is refused, the compose `neo4j` host connects.
        with mock.patch(
            "toto.ravioli.connection.Neo4jClient",
            return_value=_fake_client("bolt://neo4j:7687"),
        ):
            url = nc._bolt_url_with_credentials()
        self.assertEqual(url, "bolt://neo4j:s3cr3t@neo4j:7687")

    def test_picks_loopback_when_thats_what_connects(self):
        # Host-based dev: loopback (toto-neo4j on the host) is the reachable one.
        with mock.patch(
            "toto.ravioli.connection.Neo4jClient",
            return_value=_fake_client("bolt://127.0.0.1:7687"),
        ):
            url = nc._bolt_url_with_credentials()
        self.assertEqual(url, "bolt://neo4j:s3cr3t@127.0.0.1:7687")

    def test_falls_back_to_first_candidate_when_none_reachable(self):
        # Probe fails entirely → keep the first candidate so the self-heal retry /
        # banner surfaces the failure rather than crashing here.
        with mock.patch(
            "toto.ravioli.connection.Neo4jClient",
            return_value=_fake_client(None),
        ):
            url = nc._bolt_url_with_credentials()
        self.assertEqual(url, "bolt://neo4j:s3cr3t@127.0.0.1:7687")


@override_settings(
    RAVIOLI_ENABLED=True,
    NEO4J_USER="neo4j",
    NEO4J_PASSWORD="s3cr3t",
)
class SingleCandidateTests(TestCase):
    """When there is only one candidate (loopback disabled, or NEO4J_URI is not
    the compose host), neomodel uses it directly — no connecting probe."""

    def setUp(self):
        nc._configured_url = None
        nc._configured_for = None

    @override_settings(NEO4J_URI="bolt://neo4j:7687", RAVIOLI_NEO4J_LOCAL_FALLBACK=False)
    def test_loopback_disabled_targets_in_stack_directly_without_probe(self):
        with mock.patch("toto.ravioli.connection.Neo4jClient") as Client:
            url = nc._bolt_url_with_credentials()
        Client.assert_not_called()  # single candidate → never probes
        self.assertEqual(url, "bolt://neo4j:s3cr3t@neo4j:7687")

    @override_settings(NEO4J_URI="bolt://127.0.0.1:7687")
    def test_explicit_loopback_uri_is_single_candidate(self):
        with mock.patch("toto.ravioli.connection.Neo4jClient") as Client:
            url = nc._bolt_url_with_credentials()
        Client.assert_not_called()
        self.assertEqual(url, "bolt://neo4j:s3cr3t@127.0.0.1:7687")
