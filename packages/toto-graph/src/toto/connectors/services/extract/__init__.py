"""Extractor registry — explicit imports, mirroring ``toto.core.connectors``."""

from .base import BaseExtractor, ExtractError, ExtractResult, FetchPage  # noqa: F401

_REGISTRY = {}


def register(extractor_class):
    """Class decorator: register an extractor by its ``kind``."""
    if not issubclass(extractor_class, BaseExtractor) or not extractor_class.kind:
        raise ValueError("Extractors must subclass BaseExtractor and set a kind.")
    existing = _REGISTRY.get(extractor_class.kind)
    if existing is not None and existing is not extractor_class:
        raise ValueError(
            f"Extractor kind '{extractor_class.kind}' is already registered "
            f"by {existing.__name__}."
        )
    _REGISTRY[extractor_class.kind] = extractor_class
    return extractor_class


def get_extractor(kind):
    extractor_class = _REGISTRY.get(kind)
    if extractor_class is None:
        raise ExtractError(f"Unknown extractor kind '{kind}'.")
    return extractor_class()


def extractor_choices():
    return [
        {"kind": kind, "label": cls.label or kind}
        for kind, cls in sorted(_REGISTRY.items())
    ]


from . import rest  # noqa: E402,F401 — registration import