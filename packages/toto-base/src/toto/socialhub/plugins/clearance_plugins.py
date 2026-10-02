"""What a clearance keeps — one plugin per kind of GROUP an app keeps to
clearances (2026-09-30).

Clearances go on groups, never on single items (``clearance_access``): vault
buckets and map domains (zenobia's wiki topics went with the wiki,
2026-10-02). The socialhub's New clearance modal lets a superuser choose,
when a clearance is made, which groups it keeps. The
socialhub must not import those apps, so it asks them: each app ships
``<app>/plugins/clearance_plugins.py`` with a ``ClearanceTargetPlugin`` per
kind of group, found by ``autodiscover_plugins("plugins.clearance_plugins")``
in ``SocialHubConfig.ready`` — an app that is not installed is never imported
and its kind simply is not offered. The same mechanism as the profile and
community plugins here and the vault's ``VaultAccessPlugin``.

A plugin is data only (no template):

* ``search(q, limit)`` → the groups matching ``q``, as
  ``[{"pk", "label", "detail"}]`` — for a superuser, so nothing is filtered by
  reader; at most ``limit``;
* ``resolve(pks)`` → the groups among ``pks`` that exist (unknown pks
  ignored), in a stable order;
* ``keep(objects, clearance, actor=)`` → ADD the clearance to each group's
  clearances through the app's own door (which records the app's own audit
  record); a group's other clearances stay.

Keeping a group closes what is in it: an item in kept groups is read by
superusers and by whoever holds a clearance of every kept group it is in —
pessimistic, no owner bypass (``clearance_access``).
"""

from __future__ import annotations

from typing import ClassVar

from toto.core.plugin import BasePlugin

#: Results one search answers with.
SEARCH_LIMIT = 20


class ClearanceTargetPlugin(BasePlugin):
    registry: ClassVar[dict] = {}

    #: Font Awesome icon for the kind (without the ``fa-`` prefix).
    icon: ClassVar[str] = "file"

    def search(self, q: str, limit: int = SEARCH_LIMIT) -> list[dict]:
        raise NotImplementedError

    def resolve(self, pks) -> list:
        raise NotImplementedError

    def keep(self, objects, clearance, *, actor) -> int:
        raise NotImplementedError

    def row(self, obj) -> dict:
        """The JSON row for one object — what ``search`` answers and the
        draft keeps."""
        return {"pk": obj.pk, "label": self.label(obj), "detail": self.detail(obj)}

    def label(self, obj) -> str:
        return str(obj)

    def detail(self, obj) -> str:
        return ""


def kinds() -> list:
    """Every registered kind, in order — what the modal offers."""
    return ClearanceTargetPlugin.all()


def kind(key: str):
    """One kind by its key, or None."""
    return ClearanceTargetPlugin.get(key)


def add_to(current, clearance) -> list:
    """``current`` clearances with ``clearance`` added, once — for the apps'
    doors, which all REPLACE an object's set."""
    current = list(current)
    return current if any(c.pk == clearance.pk for c in current) else [*current, clearance]
