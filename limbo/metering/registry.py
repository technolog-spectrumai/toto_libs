"""
toto.metering.registry
======================

In-memory registry for MetricSpec declarations.

Source apps register their metrics in AppConfig.ready():

    from toto.metering.registry import metric_registry
    from toto.metering.primitives import MetricSpec

    metric_registry.register(MetricSpec(
        code="storage.transfer_mb",
        name="Storage Transfer MB",
        namespace="vault",
        default_unit="MB",
    ))

The registry is purely advisory — it documents what metrics exist.
It does NOT write to any database.
"""
from __future__ import annotations

from typing import Iterator

from .primitives import MetricSpec


class MetricRegistry:
    def __init__(self):
        self._metrics: dict[str, MetricSpec] = {}

    def register(self, spec: MetricSpec) -> None:
        self._metrics[spec.code] = spec

    def get(self, code: str) -> MetricSpec | None:
        return self._metrics.get(code)

    def all(self) -> list[MetricSpec]:
        return list(self._metrics.values())

    def by_namespace(self, namespace: str) -> list[MetricSpec]:
        return [m for m in self._metrics.values() if m.namespace == namespace]

    def codes(self) -> list[str]:
        return list(self._metrics.keys())

    def __len__(self) -> int:
        return len(self._metrics)

    def __iter__(self) -> Iterator[MetricSpec]:
        return iter(self._metrics.values())

    def __repr__(self) -> str:
        return f"MetricRegistry({len(self._metrics)} metrics)"


# Module-level singleton — import and use directly
metric_registry = MetricRegistry()
