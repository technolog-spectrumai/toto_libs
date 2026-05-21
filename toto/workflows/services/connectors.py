import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from django.conf import settings

from toto.core.connectors import (
    BaseConnector,
    ConnectorExecutionError,
    execute_connector_type,
    register_connector,
    validate_connector_type,
)

from ..models import WorkflowConnector
from ..output import normalize_workflow_output


def execute_connector(connector: WorkflowConnector, input_data: dict | None) -> dict:
    raw = execute_connector_type(
        connector.connector_type,
        connector.config or {},
        input_data or {},
    )
    wo = normalize_workflow_output(raw)
    return {"data": wo.data, "routes": wo.routes}


def validate_connector_config(connector: WorkflowConnector) -> list[str]:
    return validate_connector_type(connector.connector_type, connector.config or {})


@register_connector
class FileReadConnector(BaseConnector):
    connector_type = "file_read"
    label = "File read"
    app_label = "workflows"

    def validate(self) -> list[str]:
        if not self.config.get("path") and not self.config.get("path_field"):
            return ["File read connector requires path or path_field."]
        return []

    def execute(self, input_data: dict | None = None) -> dict:
        input_data = input_data or {}
        target = _safe_file_path(self.configured_value("path", "path_field", input_data))
        encoding = self.config.get("encoding", "utf-8")
        max_bytes = int(
            self.config.get("max_bytes")
            or getattr(settings, "WORKFLOW_FILE_CONNECTOR_MAX_BYTES", 1024 * 1024)
        )

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
        return self.apply_routes({"data": data})


@register_connector
class FileWriteConnector(BaseConnector):
    connector_type = "file_write"
    label = "File write"
    app_label = "workflows"

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.config.get("path") and not self.config.get("path_field"):
            errors.append("File write connector requires path or path_field.")
        if not any(key in self.config for key in ("content", "content_field", "json", "json_field")):
            errors.append("File write connector requires content, content_field, json, or json_field.")
        return errors

    def execute(self, input_data: dict | None = None) -> dict:
        input_data = input_data or {}
        target = _safe_file_path(self.configured_value("path", "path_field", input_data))
        encoding = self.config.get("encoding", "utf-8")
        mode = self.config.get("mode", "overwrite")
        if mode not in ("overwrite", "append"):
            raise ConnectorExecutionError("File write mode must be 'overwrite' or 'append'.")

        content = self.configured_value("content", "content_field", input_data, default=None)
        if content is None:
            content = self.configured_value("json", "json_field", input_data, default="")
            content = json.dumps(content, indent=2, sort_keys=True)
        elif not isinstance(content, str):
            content = json.dumps(content, indent=2, sort_keys=True)

        target.parent.mkdir(parents=True, exist_ok=True)
        file_mode = "a" if mode == "append" else "w"
        with target.open(file_mode, encoding=encoding) as fh:
            written = fh.write(content)

        return self.apply_routes({
            "data": {
                "path": _display_path(target),
                "bytes_written": len(content.encode(encoding)),
                "characters_written": written,
                "mode": mode,
            }
        })


@register_connector
class ApiRequestConnector(BaseConnector):
    connector_type = "api_request"
    label = "API request"
    app_label = "workflows"

    def validate(self) -> list[str]:
        if not self.config.get("url") and not self.config.get("url_field"):
            return ["API request connector requires url or url_field."]
        return []

    def execute(self, input_data: dict | None = None) -> dict:
        input_data = input_data or {}
        url = self.configured_value("url", "url_field", input_data)
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ConnectorExecutionError("API connector url must be an absolute http(s) URL.")

        allowed_hosts = getattr(settings, "WORKFLOW_API_CONNECTOR_ALLOWED_HOSTS", None)
        if allowed_hosts and parsed.hostname not in allowed_hosts:
            raise ConnectorExecutionError(f"API host {parsed.hostname!r} is not allowed.")

        params = self.configured_value("params", "params_field", input_data, default=None)
        if params:
            query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            query.update(params)
            parsed = parsed._replace(query=urlencode(query, doseq=True))
            url = urlunparse(parsed)

        method = self.config.get("method", "GET").upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ConnectorExecutionError(f"Unsupported API method: {method}")

        headers = self.configured_value("headers", "headers_field", input_data, default={}) or {}
        if not isinstance(headers, dict):
            raise ConnectorExecutionError("API connector headers must be an object.")

        body = self.configured_value("body", "body_field", input_data, default=None)
        json_payload = self.configured_value("json", "json_field", input_data, default=None)
        body_bytes = None
        if json_payload is not None:
            body_bytes = json.dumps(json_payload).encode("utf-8")
            headers = {"Content-Type": "application/json", **headers}
        elif body is not None:
            body_bytes = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")

        timeout = float(
            self.config.get("timeout_seconds")
            or getattr(settings, "WORKFLOW_API_CONNECTOR_TIMEOUT_SECONDS", 30)
        )
        max_bytes = int(
            self.config.get("max_response_bytes")
            or getattr(settings, "WORKFLOW_API_CONNECTOR_MAX_RESPONSE_BYTES", 1024 * 1024)
        )
        fail_on_http_error = self.config.get("fail_on_http_error", True)

        request = Request(url, data=body_bytes, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                return self.apply_routes(_api_response_data(response, max_bytes))
        except HTTPError as exc:
            if fail_on_http_error:
                body_text = exc.read(max_bytes).decode("utf-8", errors="replace")
                raise ConnectorExecutionError(f"API request failed with HTTP {exc.code}: {body_text}")
            return self.apply_routes(_api_response_data(exc, max_bytes))
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
