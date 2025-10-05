from django.contrib import admin
from .models import (
    Federation,
    AuthRSAKeyPair,
    FederatedIdentity,
    LocalIdentityProvider,
    ExternalIdentityProvider,
    UserFederationLink
)


@admin.register(Federation)
class FederationAdmin(admin.ModelAdmin):
    list_display = ("name", "url", "active", "created_at")
    search_fields = ("name", "url")
    list_filter = ("active",)
    readonly_fields = ("created_at",)


@admin.register(AuthRSAKeyPair)
class AuthRSAKeyPairAdmin(admin.ModelAdmin):
    list_display = ("key_id", "issuer", "active")
    search_fields = ("key_id", "issuer")
    list_filter = ("active",)


@admin.register(FederatedIdentity)
class FederatedIdentityAdmin(admin.ModelAdmin):
    list_display = ("subject", "name", "email", "issuer", "last_seen", "federation")
    search_fields = ("subject", "name", "email")
    list_filter = ("federation", "last_seen")


@admin.register(LocalIdentityProvider)
class LocalIdentityProviderAdmin(admin.ModelAdmin):
    list_display = ("name", "issuer_url", "audience", "active", "federation")
    search_fields = ("name", "issuer_url", "audience")
    list_filter = ("active", "federation")


@admin.register(ExternalIdentityProvider)
class ExternalIdentityProviderAdmin(admin.ModelAdmin):
    list_display = ("name", "issuer_url", "audience", "trusted", "federation")
    search_fields = ("name", "issuer_url", "audience")
    list_filter = ("trusted", "federation")


@admin.register(UserFederationLink)
class UserFederationLinkAdmin(admin.ModelAdmin):
    list_display = ("user", "federated_user", "linked_at", "active")
    search_fields = ("user__username", "federated_user__subject")
    list_filter = ("active", "linked_at")
