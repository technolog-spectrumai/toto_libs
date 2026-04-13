from django.conf import settings
from django.contrib import admin
from django.contrib.gis.admin import OSMGeoAdmin


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


class TotoGeoAdmin(ReadOnlyAdminMixin, OSMGeoAdmin):
    """
    Base admin for GIS models using OSMGeoAdmin.
    """
    pass
