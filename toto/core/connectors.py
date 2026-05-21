import json
from dataclasses import dataclass
from typing import Any, ClassVar

from django.db.models import Q


class ConnectorExecutionError(Exception):
    pass


_CONNECTOR_REGISTRY: dict[str, type["BaseConnector"]] = {}
_CONNECTORS_LOADED = False


def register_connector(connector_class: type["BaseConnector"]):
    _CONNECTOR_REGISTRY[connector_class.connector_type] = connector_class
    return connector_class


def get_connector_class(connector_type: str) -> type["BaseConnector"]:
    load_connector_modules()
    try:
        return _CONNECTOR_REGISTRY[connector_type]
    except KeyError as exc:
        raise ConnectorExecutionError(f"Unknown connector_type: {connector_type!r}") from exc


def execute_connector_type(connector_type: str, config: dict | None, input_data: dict | None) -> dict:
    connector = get_connector_class(connector_type)(config=config or {})
    return connector.apply_routes(connector.execute(input_data or {}))


def validate_connector_type(connector_type: str, config: dict | None) -> list[str]:
    connector = get_connector_class(connector_type)(config=config or {})
    return connector.validate()


def list_connector_types() -> list[dict[str, str]]:
    load_connector_modules()
    return [
        {"connector_type": cls.connector_type, "label": cls.label, "app_label": cls.app_label}
        for cls in sorted(_CONNECTOR_REGISTRY.values(), key=lambda item: item.connector_type)
    ]


def load_connector_modules() -> None:
    global _CONNECTORS_LOADED
    if _CONNECTORS_LOADED:
        return
    _CONNECTORS_LOADED = True

    # Import modules for registration side effects. Keep this list explicit so
    # connector availability is predictable in workflows, Mandragora, and shell use.
    for module_path in (
        "toto.workflows.services.connectors",
        "toto.api.connectors",
        "toto.people.connectors",
        "toto.socialhub.connectors",
        "toto.locations.connectors",
        "toto.events.connectors",
    ):
        try:
            __import__(module_path)
        except ImportError:
            continue


@dataclass
class BaseConnector:
    config: dict

    connector_type: ClassVar[str] = ""
    label: ClassVar[str] = ""
    app_label: ClassVar[str] = "core"

    def validate(self) -> list[str]:
        return []

    def execute(self, input_data: dict | None = None) -> dict:
        raise NotImplementedError

    def configured_value(self, literal_key: str, field_key: str, input_data: dict, default=None):
        if field_key in self.config:
            return get_dotted(input_data, self.config[field_key], default=default)
        return self.config.get(literal_key, default)

    def query_value(self, input_data: dict) -> str:
        value = self.configured_value("query", "query_field", input_data, default=None)
        if value is None:
            value = self.configured_value("q", "q_field", input_data, default="")
        return str(value or "")

    def limit(self) -> int:
        return min(max(int(self.config.get("limit", 25)), 1), 100)

    def apply_routes(self, raw: dict) -> dict:
        route = self.config.get("route")
        routes = self.config.get("routes")
        if route is not None:
            raw["route"] = route
        if routes is not None:
            raw["routes"] = routes
        return raw


class ReadOnlyModelConnector(BaseConnector):
    allowed_actions: ClassVar[set[str]] = {"list", "get", "search"}

    def validate(self) -> list[str]:
        action = self.config.get("action", "list")
        if action not in self.allowed_actions:
            return [f"Connector action must be one of: {', '.join(sorted(self.allowed_actions))}."]
        return []


def get_dotted(data: dict, path: str, default=None):
    current: Any = data
    for part in str(path).split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def filter_search(qs, connector: BaseConnector, input_data: dict, query_filter: Q):
    if connector.config.get("action", "list") == "search":
        query = connector.query_value(input_data)
        if not query:
            return qs.none()
        return qs.filter(query_filter)
    return qs


def get_object_by_config(qs, connector: BaseConnector, input_data: dict, *, default_lookup: str):
    lookup = connector.config.get("lookup", default_lookup)
    value = connector.configured_value("value", "value_field", input_data, default=None)
    if value is None:
        value = connector.configured_value(lookup, f"{lookup}_field", input_data, default=None)
    if value is None:
        raise ConnectorExecutionError(f"Read connector get action requires value or {lookup}.")
    try:
        return qs.get(**{lookup: value})
    except qs.model.DoesNotExist as exc:
        raise ConnectorExecutionError(f"{qs.model.__name__} not found for {lookup}={value!r}.") from exc
    except qs.model.MultipleObjectsReturned as exc:
        raise ConnectorExecutionError(f"Multiple {qs.model.__name__} rows found for {lookup}={value!r}.") from exc


def minimal_person(person) -> dict:
    return {
        "id": person.id,
        "uid": str(person.uid),
        "display_name": person.display_name,
        "slug": person.slug,
    }


def minimal_named(obj) -> dict:
    return {
        "id": obj.id,
        "uid": str(obj.uid),
        "name": getattr(obj, "name", str(obj)),
        "slug": getattr(obj, "slug", ""),
    }


def geometry_to_json(geometry):
    if geometry is None:
        return None
    try:
        return json.loads(geometry.geojson)
    except (AttributeError, ValueError, TypeError):
        return None


def iso(value):
    return value.isoformat() if value is not None else None
