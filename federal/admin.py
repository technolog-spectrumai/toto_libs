from django.contrib import admin
from .models import Federation, FederatedIdentity


@admin.register(Federation)
class FederationAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "active",
        "created_at",
        "platform",   # show associated platform
        "location",   # show linked address
    )
    list_filter = ("active", "created_at", "platform", "location")
    search_fields = ("name", "slug", "description", "platform__name", "location__locality_name")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at",)


@admin.register(FederatedIdentity)
class FederatedIdentityAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "federation", "user", "rsa_keypair", "created_at")
    list_filter = ("federation", "user", "created_at")
    search_fields = ("name", "id", "user__username", "user__email")
    readonly_fields = ("created_at", "qr_code")
