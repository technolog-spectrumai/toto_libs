"""The admin lists the two metering tables and nothing with a geometry.

``Address``, ``Zone`` and their link rows are not registered on purpose: a
person's point is shown by ``access.visible_point`` alone, and an admin list
would be a way around the member's "show address" switch for plain staff. It
also keeps GeoDjango's map widget, which draws tiles from an outside host,
out of every page.
"""

from django.contrib import admin

from toto.quota.admin import QuotaPolicyAdminBase, UsageEventAdminBase

from .models import GeographyQuotaPolicy, GeographyUsageEvent


@admin.register(GeographyQuotaPolicy)
class GeographyQuotaPolicyAdmin(QuotaPolicyAdminBase):
    pass


@admin.register(GeographyUsageEvent)
class GeographyUsageEventAdmin(UsageEventAdminBase):
    """Read only: an event is the record of a charge."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
