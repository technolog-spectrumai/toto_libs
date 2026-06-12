from django.contrib import admin

from .models import AsterAddress, AsterDevice


@admin.register(AsterDevice)
class AsterDeviceAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "node_id", "label", "last_seen", "created")
    list_filter = ("kind",)
    search_fields = ("user__username", "node_id", "label")
    readonly_fields = ("created", "last_seen")


@admin.register(AsterAddress)
class AsterAddressAdmin(admin.ModelAdmin):
    list_display = ("device", "relay_url", "updated_at")
    search_fields = ("device__node_id", "device__user__username", "relay_url")
    readonly_fields = ("updated_at",)
