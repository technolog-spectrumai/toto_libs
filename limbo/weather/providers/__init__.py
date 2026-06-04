from .open_meteo import OpenMeteoProvider

_REGISTRY: dict[str, type] = {
    OpenMeteoProvider.slug: OpenMeteoProvider,
}


def get_provider(slug: str):
    cls = _REGISTRY.get(slug)
    if cls is None:
        raise ValueError(f"Unknown weather provider: {slug!r}. Available: {list(_REGISTRY)}")
    return cls()


def available_providers() -> list[tuple[str, str]]:
    return [(cls.slug, cls.name) for cls in _REGISTRY.values()]
