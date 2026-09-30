"""Map domains a new clearance may keep (2026-09-30; socialhub's
``ClearanceTargetPlugin``): a domain kept to a clearance keeps every map item
in it. Added through the Domains tab's own door (``set_domain_clearances``)."""

from __future__ import annotations

from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from toto.socialhub.plugins.clearance_plugins import SEARCH_LIMIT, ClearanceTargetPlugin, add_to


@ClearanceTargetPlugin.plugin(key="locations.domain", title=_("Map domains"), order=30)
class MapDomains(ClearanceTargetPlugin):
    icon = "draw-polygon"

    def search(self, q, limit=SEARCH_LIMIT):
        from toto.locations.models import MapDomain

        return [self.row(d) for d in MapDomain.objects.filter(
            Q(name__icontains=q) | Q(slug__icontains=q) | Q(description__icontains=q))
            .order_by("name", "pk")[:limit]]

    def resolve(self, pks):
        from toto.locations.models import MapDomain

        return list(MapDomain.objects.filter(pk__in=pks).order_by("name", "pk"))

    def keep(self, objects, clearance, *, actor):
        from toto.locations.domain_views import set_domain_clearances
        from toto.socialhub.clearance_access import clearances_of

        for domain in objects:
            set_domain_clearances(domain, add_to(clearances_of(domain, rows="clearance_rows"), clearance),
                                  actor=actor)
        return len(objects)

    def label(self, obj):
        return obj.name

    def detail(self, obj):
        return (obj.description or "")[:80]
