from django.contrib import admin

from .models import SSOAccessToken, SSOAuthorizationCode, SSOClient, SSOSubject


@admin.register(SSOClient)
class SSOClientAdmin(admin.ModelAdmin):
    list_display = ["name", "client_id", "client_type", "active", "trusted", "created_at"]
    list_filter = ["client_type", "active", "trusted"]
    search_fields = ["name", "client_id"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(SSOSubject)
class SSOSubjectAdmin(admin.ModelAdmin):
    list_display = ["user", "subject", "created_at"]
    search_fields = ["user__username", "user__email", "subject"]
    readonly_fields = ["subject", "created_at"]


@admin.register(SSOAuthorizationCode)
class SSOAuthorizationCodeAdmin(admin.ModelAdmin):
    list_display = ["client", "user", "created_at", "expires_at", "used_at"]
    list_filter = ["client", "created_at", "used_at"]
    search_fields = ["client__name", "client__client_id", "user__username", "user__email"]
    readonly_fields = ["code", "created_at", "expires_at", "used_at"]


@admin.register(SSOAccessToken)
class SSOAccessTokenAdmin(admin.ModelAdmin):
    list_display = ["client", "user", "scope", "created_at", "expires_at", "revoked_at"]
    list_filter = ["client", "created_at", "expires_at", "revoked_at"]
    search_fields = ["client__name", "client__client_id", "user__username", "user__email"]
    readonly_fields = ["token", "created_at", "expires_at", "revoked_at"]
