import json
from pathlib import Path
from typing import Any

from django.conf import settings


def _get_root() -> Path:
    root = getattr(settings, "WORKFLOW_FILE_CONNECTOR_ROOT", None)
    if root is None:
        root = Path(settings.MEDIA_ROOT) / "workflow-files"
    return Path(root).resolve()


def _safe_path(root: Path, raw: str) -> Path:
    path = Path(str(raw))
    target = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise PermissionError(f"Path escapes the file root: {raw!r}")
    return target


class FileClient:
    """
    Safe file read/write utility for notebook cells and workflow lambdas.

    Available as the ``files`` variable in every cell and lambda execution.
    All paths are resolved relative to WORKFLOW_FILE_CONNECTOR_ROOT
    (defaults to MEDIA_ROOT/workflow-files).

    Usage::

        content = files.read("inputs/data.json")
        data = files.read_json("inputs/data.json")
        files.write("outputs/result.txt", "hello")
        files.write_json("outputs/result.json", {"key": "value"})
        files.append("logs/run.log", "step done\\n")
        files.exists("inputs/data.json")  # True / False
        files.list("outputs/")            # ["outputs/result.txt", ...]
        files.delete("tmp/scratch.txt")
    """

    def __init__(self, root: "str | Path | None" = None):
        self._root = Path(root).resolve() if root else _get_root()

    # ------------------------------------------------------------------ read

    def read(self, path: str, *, encoding: str = "utf-8", max_bytes: int | None = None) -> str:
        target = _safe_path(self._root, path)
        limit = max_bytes or getattr(settings, "WORKFLOW_FILE_CONNECTOR_MAX_BYTES", 1024 * 1024)
        with target.open("rb") as fh:
            return fh.read(limit).decode(encoding)

    def read_json(self, path: str) -> Any:
        return json.loads(self.read(path))

    def read_bytes(self, path: str, max_bytes: int | None = None) -> bytes:
        target = _safe_path(self._root, path)
        limit = max_bytes or getattr(settings, "WORKFLOW_FILE_CONNECTOR_MAX_BYTES", 1024 * 1024)
        with target.open("rb") as fh:
            return fh.read(limit)

    # ----------------------------------------------------------------- write

    def write(self, path: str, content: "str | bytes", *, encoding: str = "utf-8") -> int:
        target = _safe_path(self._root, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
            return len(content)
        target.write_text(content, encoding=encoding)
        return len(content.encode(encoding))

    def write_json(self, path: str, data: Any, *, indent: int = 2) -> int:
        return self.write(path, json.dumps(data, indent=indent, sort_keys=True))

    def append(self, path: str, content: str, *, encoding: str = "utf-8") -> int:
        target = _safe_path(self._root, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding=encoding) as fh:
            fh.write(content)
        return len(content.encode(encoding))

    # ----------------------------------------------------------------- query

    def exists(self, path: str) -> bool:
        try:
            return _safe_path(self._root, path).exists()
        except PermissionError:
            return False

    def list(self, directory: str = "", pattern: str = "*") -> list:
        dir_path = _safe_path(self._root, directory) if directory else self._root
        if not dir_path.is_dir():
            return []
        return [
            str(p.relative_to(self._root))
            for p in sorted(dir_path.glob(pattern))
            if p.is_file()
        ]

    def delete(self, path: str) -> bool:
        target = _safe_path(self._root, path)
        if target.exists():
            target.unlink()
            return True
        return False

    def __repr__(self) -> str:
        return f"FileClient(root={str(self._root)!r})"
