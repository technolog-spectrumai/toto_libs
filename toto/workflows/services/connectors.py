import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl
from urllib.request import Request, urlopen

from django.conf import settings

from ..models import WorkflowConnector
from ..output import normalize_workflow_output


class ConnectorExecutionError(Exception):
    pass


def execute_connector(connector: WorkflowConnector, input_data: dict | None) -> dict:
    config = connector.config or {}
    input_data = input_data or {}

    if connector.connector_type == WorkflowConnector.FILE_READ:
        raw = _execute_file_read(config, input_data)
    elif connector.connector_type == WorkflowConnector.FILE_WRITE:
        raw = _execute_file_write(config, input_data)
    elif connector.connector_type == WorkflowConnector.API_REQUEST:
        raw = _execute_api_request(config, input_data)
    else:
        raise ConnectorExecutionError(f"Unknown connector_type: {connector.connector_type!r}")

    route = config.get("route")
    routes = config.get("routes")
    if route is not None:
        raw["route"] = route
    if routes is not None:
        raw["routes"] = routes

    wo = normalize_workflow_output(raw)
    return {"data": wo.data, "routes": wo.routes}


def validate_connector_config(connector: WorkflowConnector) -> list[str]:
    errors: list[str] = []
    config = connector.config or {}

    if connector.connector_type in (WorkflowConnector.FILE_READ, WorkflowConnector.FILE_WRITE):
        if not config.get("path") and not config.get("path_field"):
            errors.append(f"Connector {connector.id} ({connector.name!r}) requires config['path'] or config['path_field'].")
    if connector.connector_type == WorkflowConnector.FILE_WRITE:
        if not any(key in config for key in ("content", "content_field", "json", "json_field")):
            errors.append(
                f"Connector {connector.id} ({connector.name!r}) requires content, content_field, json, or json_field."
            )
    if connector.connector_type == WorkflowConnector.API_REQUEST:
        if not config.get("url") and not config.get("url_field"):
            errors.append(f"Connector {connector.id} ({connector.name!r}) requires config['url'] or config['url_field'].")

    return errors


def _execute_file_read(config: dict, input_data: dict) -> dict:
    target = _safe_file_path(_configured_value(config, input_data, "path", "path_field"))
    encoding = config.get("encoding", "utf-8")
    max_bytes = int(config.get("max_bytes") or getattr(settings, "WORKFLOW_FILE_CONNECTOR_MAX_BYTES", 1024 * 1024))

    with target.open("rb") as fh:
        raw = fh.read(max_bytes + 1)
    truncated = len(raw) > max_bytes
    if truncated:
        raw = raw[:max_bytes]

    content = raw.decode(encoding)
    data = {
        "path": _display_path(target),
        "content": content,
        "bytes": len(raw),
        "truncated": truncated,
    }
    try:
        data["json"] = json.loads(content)
    except ValueError:
        pass
    return {"data": data}


def _execute_file_write(config: dict, input_data: dict) -> dict:
    target = _safe_file_path(_configured_value(config, input_data, "path", "path_field"))
    encoding = config.get("encoding", "utf-8")
    mode = config.get("mode", "overwrite")
    if mode not in ("overwrite", "append"):
        raise ConnectorExecutionError("File write mode must be 'overwrite' or 'append'.")

    content = _configured_value(config, input_data, "content", "content_field", default=None)
    if content is None:
        content = _configured_value(config, input_data, "json", "json_field", default="")
        content = json.dumps(content, indent=2, sort_keys=True)
    elif not isinstance(content, str):
        content = json.dumps(content, indent=2, sort_keys=True)

    target.parent.mkdir(parents=True, exist_ok=True)
    file_mode = "a" if mode == "append" else "w"
    with target.open(file_mode, encoding=encoding) as fh:
        written = fh.write(content)

    return {
        "data": {
            "path": _display_path(target),
            "bytes_written": len(content.encode(encoding)),
            "characters_written": written,
            "mode": mode,
        }
    }


def _execute_api_request(config: dict, input_data: dict) -> dict:
    url = _configured_value(config, input_data, "url", "url_field")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConnectorExecutionError("API connector url must be an absolute http(s) URL.")

    allowed_hosts = getattr(settings, "WORKFLOW_API_CONNECTOR_ALLOWED_HOSTS", None)
    if allowed_hosts and parsed.hostname not in allowed_hosts:
        raise ConnectorExecutionError(f"API host {parsed.hostname!r} is not allowed.")

    params = _configured_value(config, input_data, "params", "params_field", default=None)
    if params:
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query.update(params)
        parsed = parsed._replace(query=urlencode(query, doseq=True))
        url = urlunparse(parsed)

    method = config.get("method", "GET").upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise ConnectorExecutionError(f"Unsupported API method: {method}")

    headers = _configured_value(config, input_data, "headers", "headers_field", default={}) or {}
    if not isinstance(headers, dict):
        raise ConnectorExecutionError("API connector headers must be an object.")

    body = _configured_value(config, input_data, "body", "body_field", default=None)
    json_payload = _configured_value(config, input_data, "json", "json_field", default=None)
    body_bytes = None
    if json_payload is not None:
        body_bytes = json.dumps(json_payload).encode("utf-8")
        headers = {"Content-Type": "application/json", **headers}
    elif body is not None:
        body_bytes = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")

    timeout = float(config.get("timeout_seconds") or getattr(settings, "WORKFLOW_API_CONNECTOR_TIMEOUT_SECONDS", 30))
    max_bytes = int(config.get("max_response_bytes") or getattr(settings, "WORKFLOW_API_CONNECTOR_MAX_RESPONSE_BYTES", 1024 * 1024))
    fail_on_http_error = config.get("fail_on_http_error", True)

    request = Request(url, data=body_bytes, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return _api_response_data(response, max_bytes)
    except HTTPError as exc:
        if fail_on_http_error:
            body_text = exc.read(max_bytes).decode("utf-8", errors="replace")
            raise ConnectorExecutionError(f"API request failed with HTTP {exc.code}: {body_text}")
        return _api_response_data(exc, max_bytes)
    except URLError as exc:
        raise ConnectorExecutionError(f"API request failed: {exc.reason}") from exc


def _api_response_data(response, max_bytes: int) -> dict:
    raw = response.read(max_bytes + 1)
    truncated = len(raw) > max_bytes
    if truncated:
        raw = raw[:max_bytes]
    body = raw.decode("utf-8", errors="replace")
    data = {
        "status_code": getattr(response, "status", response.getcode()),
        "headers": dict(response.headers.items()),
        "body": body,
        "truncated": truncated,
    }
    try:
        data["json"] = json.loads(body)
    except ValueError:
        pass
    return {"data": data}


def _configured_value(config: dict, input_data: dict, literal_key: str, field_key: str, default=None):
    if field_key in config:
        return _get_dotted(input_data, config[field_key], default=default)
    return config.get(literal_key, default)


def _get_dotted(data: dict, path: str, default=None):
    current = data
    for part in str(path).split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def _connector_root() -> Path:
    root = getattr(settings, "WORKFLOW_FILE_CONNECTOR_ROOT", None)
    if root is None:
        root = Path(settings.MEDIA_ROOT) / "workflow-files"
    return Path(root).resolve()


def _safe_file_path(raw_path) -> Path:
    if not raw_path:
        raise ConnectorExecutionError("File connector path is required.")
    root = _connector_root()
    path = Path(str(raw_path))
    target = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ConnectorExecutionError("File connector path escapes WORKFLOW_FILE_CONNECTOR_ROOT.") from exc
    return target


def _display_path(path: Path) -> str:
    root = _connector_root()
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
