from django.contrib import admin
from .models import (
    AuthRSAKeyPair,
    IdentityProvider,
    FederatedIdentity,
    UserFederationLink
)


@admin.register(AuthRSAKeyPair)
class RSAKeyPairAdmin(admin.ModelAdmin):
    list_display = ('key_id', 'issuer', 'active', 'created_at')
    search_fields = ('key_id', 'issuer')
    list_filter = ('active',)


@admin.register(IdentityProvider)
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
