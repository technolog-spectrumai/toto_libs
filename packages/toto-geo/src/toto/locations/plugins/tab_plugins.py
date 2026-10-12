"""Tabs another app adds to the Locations bar (2026-10-12).

The bar in ``locations/base.html`` names the library's own tabs and the
library apps it knows (Places, Travels, Weather). A host app with a page
that belongs to the map — the things it tracks, listed with their pins —
registers a tab here in its ``ready()`` instead of overriding the frame:

    LocationTabPlugin.register(key="objects", label=_("Objects"),
                               icon="fa-location-pin", url_name="wotan:objects",
                               namespace="wotan", url_names=("objects",))

The tab is drawn after the library's, active when the page's namespace is
``namespace`` (and, when ``url_names`` is given, its url name one of them),
shown only when ``visible(user)`` says so (default: a signed-in user). A
tab whose url cannot be reversed is left out, never a broken link.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from django.urls import NoReverseMatch, reverse


def _signed_in(user) -> bool:
    return bool(user is not None and user.is_authenticated)


@dataclass(frozen=True)
class LocationTab:
    key: str
    label: str
    icon: str
    url_name: str
    namespace: str = ""
    url_names: tuple = ()
    visible: Callable = _signed_in
    order: int = 100
    url_args: tuple = field(default_factory=tuple)

    def is_active(self, match) -> bool:
        if match is None or not self.namespace or match.namespace != self.namespace:
            return False
        return not self.url_names or match.url_name in self.url_names


class LocationTabPlugin:
    _tabs: dict = {}

    @classmethod
    def register(cls, *, key, label, icon, url_name, namespace="", url_names=(), visible=None,
                 order=100, url_args=()):
        """Add (or replace, by ``key``) one tab."""
        cls._tabs[key] = LocationTab(key=key, label=label, icon=icon, url_name=url_name,
                                     namespace=namespace, url_names=tuple(url_names),
                                     visible=visible or _signed_in, order=order,
                                     url_args=tuple(url_args))

    @classmethod
    def unregister(cls, key):
        cls._tabs.pop(key, None)

    @classmethod
    def tabs(cls, request) -> list[dict]:
        """The tabs this viewer sees, in order, each {key, label, icon, url,
        active}."""
        user = getattr(request, "user", None)
        match = getattr(request, "resolver_match", None)
        out = []
        for tab in sorted(cls._tabs.values(), key=lambda t: (t.order, t.key)):
            try:
                if not tab.visible(user):
                    continue
                url = reverse(tab.url_name, args=tab.url_args)
            except NoReverseMatch:
                continue
            out.append({"key": tab.key, "label": tab.label, "icon": tab.icon, "url": url,
                        "active": tab.is_active(match)})
        return out
