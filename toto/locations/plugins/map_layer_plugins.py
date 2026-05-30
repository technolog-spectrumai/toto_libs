"""
LocationMapLayerPlugin — registry for virtual map layers provided by external apps.

Each registered callable is called with no arguments and must return either:
  - A single layer dict matching map_layer_payload structure, or
  - A list of layer dicts, or
  - None (skip silently)

Required layer dict keys:
  id, name, slug, description, unit, min_value, max_value,
  style, inverted_importance, half_range, polygons

Each polygon dict must have:
  id, name, value, properties, center (GeoJSON Point or None), geometry (GeoJSON)
"""
from __future__ import annotations

import logging

logger = logging.getLogger("locations.map_layers")


class LocationMapLayerPlugin:
    _providers: list = []

    @classmethod
    def register(cls, func):
        cls._providers.append(func)
        return func

    @classmethod
    def get_layers(cls) -> list[dict]:
        layers = []
        for provider in cls._providers:
            try:
                result = provider()
                if result is None:
                    continue
                if isinstance(result, list):
                    layers.extend(r for r in result if r)
                else:
                    layers.append(result)
            except Exception:
                logger.exception("Map layer provider %s raised an error", provider)
        return layers
