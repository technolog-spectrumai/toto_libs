from django.contrib import admin
from .models import (
    RSAKey,
    TrustedIssuer,
    FederatedIdentity,
    UserFederationLink,
    IdentityToken,
    AccessToken
)


@admin.register(RSAKey)
class RSAKeyAdmin(admin.ModelAdmin):
    list_display = ('key_id', 'issuer', 'active', 'created_at')
    search_fields = ('key_id', 'issuer')
    list_filter = ('active',)


@admin.register(TrustedIssuer)
class TrustedIssuerAdmin(admin.ModelAdmin):
    list_display = ('name', 'issuer_url', 'audience', 'trusted')
    search_fields = ('name', 'issuer_url')
    list_filter = ('trusted',)


@admin.register(FederatedIdentity)
class FederatedIdentityAdmin(admin.ModelAdmin):
    list_display = ('subject', 'issuer', 'email', 'name', 'last_seen')
    search_fields = ('subject', 'email', 'name')
    list_filter = ('issuer',)


@admin.register(UserFederationLink)
class UserFederationLinkAdmin(admin.ModelAdmin):
    list_display = ('user', 'federated_user', 'linked_at', 'active')
    search_fields = ('user__username', 'federated_user__subject')
    list_filter = ('active',)


@admin.register(IdentityToken)
class IdentityTokenAdmin(admin.ModelAdmin):
    list_display = ('user', 'audience', 'issuer', 'issued_at', 'expires_at')
    search_fields = ('user__username', 'audience', 'issuer')
    list_filter = ('issuer',)


@admin.register(AccessToken)
class AccessTokenAdmin(admin.ModelAdmin):
    list_display = ('user', 'audience', 'issuer', 'issued_at', 'expires_at')
    search_fields = ('user__username', 'audience', 'issuer')
    list_filter = ('issuer',)
