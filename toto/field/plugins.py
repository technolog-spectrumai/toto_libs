"""
Field Command plugin system.

Upstream apps register providers in their AppConfig.ready(); field views call
the registries at request time. Field has zero direct imports of upstream models.

────────────────────────────────────────────────────────────
FieldMapPlugin
────────────────────────────────────────────────────────────
Each provider is a callable:

    def my_features(request=None) -> list[dict]:
        # Return a list of GeoJSON Feature dicts.
        # Each feature MUST have a "layer" key in properties.
        return [{"type": "Feature", "geometry": {...}, "properties": {"layer": "my_layer", ...}}]

    FieldMapPlugin.register(my_features)

────────────────────────────────────────────────────────────
FieldMetricsPlugin
────────────────────────────────────────────────────────────
Each provider is a callable:

    def my_section(request=None) -> dict | None:
        return {
            "key":     "my_app",         # unique, used as HTML id
            "title":   "My App",         # section heading
            "order":   30,               # render order (lower = first)
            "app_url": "/my-app/",       # link to the upstream app (read-only ref)
            "ribbon": [                  # ≤4 items shown in command ribbon
                {"label": "Things", "value": 42, "alert": False},
            ],
            "kpis": [                    # cards on the metrics page
                {"label": "Total things", "value": 42, "sub": "active", "alert": False},
            ],
            "chart": None,              # or {"type": "bar"|"doughnut",
                                        #      "labels": [...], "data": [...],
                                        #      "colors": [...]}   (colors optional)
            "table": None,              # or {"headers": ["Col A", "Col B"],
                                        #      "rows":    [["a", 1], ["b", 2]]}
        }

    FieldMetricsPlugin.register(my_section)
"""
from __future__ import annotations


class FieldMapPlugin:
    """Registry for field command map feature providers."""

    _providers: list = []

    @classmethod
    def register(cls, func):
        cls._providers.append(func)

    @classmethod
    def get_features(cls, request=None) -> list[dict]:
        features = []
        for provider in cls._providers:
            try:
                features.extend(provider(request=request))
            except Exception:
                pass
        return features


class FieldMetricsPlugin:
    """Registry for field command metrics section providers."""

    _providers: list = []

    @classmethod
    def register(cls, func):
        cls._providers.append(func)

    @classmethod
    def get_sections(cls, request=None) -> list[dict]:
        sections = []
        for provider in cls._providers:
            try:
                result = provider(request=request)
                if result:
                    sections.append(result)
            except Exception:
                pass
        return sorted(sections, key=lambda s: s.get("order", 100))

    @classmethod
    def get_ribbon(cls, request=None) -> list[dict]:
        """Flat list of ribbon KPI dicts from all sections, in section order."""
        ribbon = []
        for section in cls.get_sections(request=request):
            for item in section.get("ribbon") or []:
                ribbon.append({**item, "_section": section.get("title", "")})
        return ribbon
