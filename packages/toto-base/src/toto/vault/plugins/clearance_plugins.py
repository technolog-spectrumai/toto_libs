"""Buckets a new clearance may keep (2026-09-30; socialhub's
``ClearanceTargetPlugin``): a bucket kept to a clearance keeps every file in
it. Added through the vault's own door (``clearances.set_clearances``)."""

from __future__ import annotations

from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from toto.socialhub.plugins.clearance_plugins import SEARCH_LIMIT, ClearanceTargetPlugin, add_to


@ClearanceTargetPlugin.plugin(key="vault.bucket", title=_("Buckets"), order=20)
class Buckets(ClearanceTargetPlugin):
    icon = "bucket"

    def _buckets(self):
        from toto.vault.models import Bucket

        return Bucket.objects.select_related("owner")

    def search(self, q, limit=SEARCH_LIMIT):
        return [self.row(b) for b in self._buckets().filter(Q(name__icontains=q) | Q(slug__icontains=q))
                .order_by("name", "pk")[:limit]]

    def resolve(self, pks):
        return list(self._buckets().filter(pk__in=pks).order_by("name", "pk"))

    def keep(self, objects, clearance, *, actor):
        from toto.vault.clearances import clearances_of, set_clearances

        for bucket in objects:
            set_clearances(bucket, add_to(clearances_of(bucket), clearance), actor=actor)
        return len(objects)

    def label(self, obj):
        return obj.name

    def detail(self, obj):
        return obj.owner.get_username() if obj.owner_id else ""
