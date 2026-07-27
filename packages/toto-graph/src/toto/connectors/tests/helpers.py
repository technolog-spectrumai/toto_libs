"""Shared fixtures for the connectors test suite (ingestor-test idioms)."""

from toto.api.models import Connector
from toto.bento.models import BentoCategory, BentoEdgeType
from toto.connectors.models import DataConnector
from toto.ingestor.services.catalog import CatalogEntry
from toto.ingestor.services.text import normalize

DEFAULT_EXTRACT_CONFIG = {
    "endpoint": "items",
    "method": "GET",
    "records_path": "results",
    "pagination": {"strategy": "none"},
}

DEFAULT_MAPPING_SPEC = {
    "version": 1,
    "nodes": [
        {
            "rule_id": "person",
            "category_slug": "person",
            "identifier": {"path": "author.name"},
            "properties": {"name": {"path": "author.name"}},
        },
        {
            "rule_id": "org",
            "category_slug": "org",
            "identifier": {"path": "org.name"},
            "properties": {"name": {"path": "org.name"}},
        },
    ],
    "relationships": [
        {
            "rule_id": "works-at",
            "edge_type_slug": "works-at",
            "from_rule": "person",
            "to_rule": "org",
            "properties": {},
        }
    ],
}


def make_bento_templates():
    """person/org categories + a works-at edge (person → org)."""
    person = BentoCategory.objects.create(
        name="Person",
        slug="person",
        property_schema=[
            {"name": "name", "type": "string", "required": True},
            {"name": "note", "type": "text", "required": False},
        ],
    )
    org = BentoCategory.objects.create(
        name="Org",
        slug="org",
        property_schema=[{"name": "name", "type": "string", "required": True}],
    )
    works_at = BentoEdgeType.objects.create(
        name="Works at",
        slug="works-at",
        rel_type="WORKS_AT",
        property_schema=[{"name": "year", "type": "integer", "required": False}],
    )
    works_at.allowed_sources.set([person])
    works_at.allowed_targets.set([org])
    return person, org, works_at


def make_http_connector(**overrides):
    defaults = {
        "name": "Example API",
        "base_url": "https://api.example.com/",
        "auth_type": Connector.AUTH_NONE,
        "provider": Connector.PROVIDER_GENERIC,
    }
    defaults.update(overrides)
    return Connector.objects.create(**defaults)


def make_data_connector(http_connector=None, **overrides):
    defaults = {
        "name": "Demo connector",
        "extract_config": DEFAULT_EXTRACT_CONFIG,
        "mapping_spec": DEFAULT_MAPPING_SPEC,
    }
    defaults.update(overrides)
    if http_connector is None:
        # api.Connector.name is unique — derive it from this connector's name.
        http_connector = make_http_connector(name=f"API for {defaults['name']}")
    defaults["http_connector"] = http_connector
    return DataConnector.objects.create(**defaults)


def entry(surface, uid, slug):
    return CatalogEntry(surface, normalize(surface), uid, slug, surface, "name")


BENTO_CATEGORIES = {"person": "Person", "org": "Org"}
BENTO_EDGE_TYPES = {"works-at": {"name": "Works at", "rel_type": "WORKS_AT"}}
BENTO_SEARCHABLE = {"person": "name", "org": "name"}
