import json
from pathlib import Path

from django.conf import settings

from toto.core.connectors import (
    BaseConnector,
    ConnectorExecutionError,
    register_connector,
)


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
