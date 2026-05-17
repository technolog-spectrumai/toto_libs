from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from django.conf import settings


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "economy.yaml"


@lru_cache(maxsize=4)
def load_engine_config(config_path: str | None = None) -> dict[str, Any]:
    configured_path = config_path or getattr(settings, "GAME_ENGINE_CONFIG_PATH", "") or DEFAULT_CONFIG_PATH
    path = Path(configured_path)
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Game engine config must be a YAML mapping: {path}")
    return data


def engine_value(key: str, default: Any = None) -> Any:
    return load_engine_config().get("engine", {}).get(key, default)


def ticks_per_day() -> int:
    return int(engine_value("ticks_per_day", 288))


def inventory_capacity() -> str:
    return str(engine_value("default_inventory_capacity", 100000))


def level_multiplier(level: int) -> float:
    base = float(engine_value("building_level_base_multiplier", 1.0))
    growth = float(engine_value("building_level_growth", 0.35))
    return base + growth * max(0, level - 1)


def construction_required_work(target_level: int) -> float:
    base = float(engine_value("construction_work_base", 50))
    per_level = float(engine_value("construction_work_per_level", 50))
    return base + per_level * max(1, target_level)


def policy_modifier(policy: str, modifier: str, default: float = 1.0) -> float:
    return float(load_engine_config().get("policy_modifiers", {}).get(policy, {}).get(modifier, default))


def specialization_modifier(specialization: str, modifier: str, default: float = 1.0) -> float:
    return float(load_engine_config().get("specialization_modifiers", {}).get(specialization, {}).get(modifier, default))
