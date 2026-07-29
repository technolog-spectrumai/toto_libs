"""Shared form-widget helpers. Each command's form lives in its command file."""

from django import forms  # noqa: F401  (re-exported for command modules)

_INPUT_CLASS = (
    "w-full rounded-lg border px-3 py-2 text-sm "
    "focus:outline-none focus:ring-2 focus:ring-offset-1"
)
_XBIND = (
    "darkMode "
    "? 'bg-primary-bg-dark border-accent-1 text-text-main-dark focus:ring-accent-1' "
    ": 'bg-primary-bg-light border-accent-2 text-text-main-light focus:ring-accent-2'"
)


def _w(extra_attrs: dict | None = None):
    attrs = {"class": _INPUT_CLASS, "x-bind:class": _XBIND}
    if extra_attrs:
        attrs.update(extra_attrs)
    return attrs
