from toto.core.connectors import (
    execute_connector_type,
    list_connector_types,
    validate_connector_type,
)


def list_available_connectors() -> list[dict[str, str]]:
    return list_connector_types()


def execute_connector(connector_type: str, config: dict | None = None, input_data: dict | None = None) -> dict:
    return execute_connector_type(connector_type, config or {}, input_data or {})


def validate_connector(connector_type: str, config: dict | None = None) -> list[str]:
    return validate_connector_type(connector_type, config or {})
