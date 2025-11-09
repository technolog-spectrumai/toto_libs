from django.contrib import admin
from .models import Federation, FederatedIdentity


@admin.register(Federation)
class FederationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "active", "created_at")
    list_filter = ("active", "created_at")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}  # auto-fill slug from name


@admin.register(FederatedIdentity)
class FederatedIdentityAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "federation", "rsa_keypair", "created_at")
    list_filter = ("federation", "created_at")
    search_fields = ("name", "id")
    readonly_fields = ("created_at", "qr_code")


