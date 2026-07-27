"""Tiny shared text helpers (deterministic)."""


def normalize(value):
    """Lowercase, collapse whitespace — the canonical key for matching surfaces."""
    return " ".join((value or "").strip().lower().split())
