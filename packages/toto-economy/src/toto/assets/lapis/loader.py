"""Load and dump Lapis contracts as JSON or YAML.

Lapis is canonicalized internally as a Python dict / JSON tree. YAML is only a
human-friendly serialization format.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from .exceptions import LapisValidationError

Format = Literal["json", "yaml"]


def _yaml_module():
    try:
        import yaml  # type: ignore
    except Exception as exc:  # pragma: no cover - environment dependent
        raise LapisValidationError(
            "YAML support requires PyYAML. Install with: pip install pyyaml"
        ) from exc
    return yaml


def detect_format(value: str | Path, *, default: Format = "json") -> Format:
    """Detect JSON/YAML from extension or text content."""
    if isinstance(value, Path) or (isinstance(value, str) and len(value) < 512):
        suffix = Path(value).suffix.lower()
        if suffix == ".json":
            return "json"
        if suffix in {".yaml", ".yml"}:
            return "yaml"

    text = str(value).lstrip()
    if text.startswith("{") or text.startswith("["):
        return "json"
    if text.startswith("---") or "\n" in text:
        return "yaml"
    return default


def loads_contract(text: str, *, fmt: Format | None = None) -> dict[str, Any]:
    fmt = fmt or detect_format(text)
    try:
        if fmt == "json":
            data = json.loads(text)
        elif fmt == "yaml":
            data = _yaml_module().safe_load(text)
        else:
            raise LapisValidationError(f"Unsupported Lapis format: {fmt!r}")
    except Exception as exc:
        if isinstance(exc, LapisValidationError):
            raise
        raise LapisValidationError(f"Could not parse Lapis {fmt}: {exc}") from exc

    if not isinstance(data, dict):
        raise LapisValidationError("Lapis contract must parse to a JSON object/dict.")
    return data


def load_contract(path: str | Path, *, fmt: Format | None = None) -> dict[str, Any]:
    path = Path(path)
    return loads_contract(path.read_text(encoding="utf-8"), fmt=fmt or detect_format(path))


def dumps_contract(contract: dict[str, Any], *, fmt: Format = "json", pretty: bool = True) -> str:
    if fmt == "json":
        return json.dumps(contract, indent=2 if pretty else None, sort_keys=pretty)
    if fmt == "yaml":
        return _yaml_module().safe_dump(contract, sort_keys=False, allow_unicode=True)
    raise LapisValidationError(f"Unsupported Lapis format: {fmt!r}")


def dump_contract(contract: dict[str, Any], path: str | Path, *, fmt: Format | None = None) -> None:
    path = Path(path)
    fmt = fmt or detect_format(path)
    path.write_text(dumps_contract(contract, fmt=fmt), encoding="utf-8")
