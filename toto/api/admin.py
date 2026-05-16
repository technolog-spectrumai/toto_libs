from django.contrib import admin

from .models import Connector, EmailService


@admin.register(Connector)
class ConnectorAdmin(admin.ModelAdmin):
    list_display = ("name", "provider", "auth_type", "api_secret", "signing_key", "is_active", "updated_at")
    list_filter = ("provider", "auth_type", "is_active", "created_at")
    search_fields = ("name", "slug", "base_url")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("api_secret", "signing_key", "owner")
    fieldsets = (
        (None, {
            "fields": ("name", "slug", "provider", "base_url", "is_active", "owner"),
        }),
        ("Authentication", {
            "fields": ("auth_type", "api_secret", "signing_key", "auth_config"),
            "description": "Link secrets to a Gervazy EncryptedSecret. Decryption requires a vault session.",
        }),
        ("Extra config", {
            "fields": ("extra",),
            "description": "Non-secret provider-specific configuration. Do not store secrets here.",
            "classes": ("collapse",),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )


@admin.register(EmailService)
class EmailServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "email_address", "host", "port", "use_tls", "use_ssl", "created_at")
    search_fields = ("name", "email_address", "host")
    readonly_fields = ("id", "created_at")
