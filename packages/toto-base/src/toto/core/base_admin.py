from django.conf import settings
from django.contrib import admin

# On a GIS build, every geometry field in the admin is drawn by LocalOSMWidget
# (MapWidgetMixin, below). On a GIS-off build there is no GDAL to import
# contrib.gis, so the mixin is empty and TotoGeoAdmin a plain ModelAdmin.
# This import runs at startup on every host (admin autodiscovery), so it must
# never hard-require GDAL. See the suite README, "Making GIS optional".
#
# The map from this platform (2026-10-01, 37c.20). OSMGeoAdmin, the base
# until then, fetched OpenLayers 2 from cdnjs.cloudflare.com, and an inline
# row's geometry (Django's default widget) OpenLayers 7 from cdn.jsdelivr.net
# over NASA's tiles: each told that website the address of every staff member
# who opened the page. OSMGeoAdmin leaves in Django 5.0 anyway; GISModelAdmin's
# OSMWidget is its successor, here with the image's own OpenLayers
# (download_vendor.py, `vendor/openlayers/`, the 7.2.2 Django names) and
# OpenStreetMap's tiles, the map's one outside source.
LocalOSMWidget = None
_MapWidgetBase = object
if getattr(settings, "HAS_GIS", True):
    try:
        from django.contrib.gis.admin.options import GeoModelAdminMixin
        from django.contrib.gis.forms.widgets import OSMWidget
    except Exception:
        pass
    else:
        class LocalOSMWidget(OSMWidget):
            """Django's OpenStreetMap widget, its OpenLayers from /static/."""

            class Media:
                extend = False
                css = {"all": ("vendor/openlayers/ol.css", "gis/css/ol3.css")}
                js = ("vendor/openlayers/ol.js", "gis/js/OLMapWidget.js")

        class _MapWidgetBase(GeoModelAdminMixin):
            gis_widget = LocalOSMWidget


class MapWidgetMixin(_MapWidgetBase):
    """Draws a ModelAdmin's or an inline's geometry fields with LocalOSMWidget.

    An inline needs it as much as a ModelAdmin: its rows are drawn by the
    inline's own formfield_for_dbfield, which no ModelAdmin's mixin reaches.
    """


class ReadOnlyAdminMixin:
    """
    Makes admin read-only when ADMIN_READONLY=True in settings.
    """
    readonly_flag = getattr(settings, "TOTO_ADMIN_READONLY", False)

    def has_add_permission(self, request):
        if self.readonly_flag:
            return False
        return super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        if self.readonly_flag:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if self.readonly_flag:
            return False
        return super().has_delete_permission(request, obj)

    def get_readonly_fields(self, request, obj=None):
        if self.readonly_flag:
            return [f.name for f in self.model._meta.fields]
        return super().get_readonly_fields(request, obj)


class TotoModelAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """
    Base admin for normal Django models.
    """
    pass


class TotoGeoAdmin(ReadOnlyAdminMixin, MapWidgetMixin, admin.ModelAdmin):
    """
    Base admin for GIS models: GISModelAdmin's map (LocalOSMWidget) on a GIS
    build, a plain ModelAdmin when GIS is off.
    """
    pass
