from django.contrib import admin
from django.utils.html import format_html
from .models import (
    Federation,
    AuthRSAKeyPair,
    LocalIdentityProvider,
    ExternalIdentityProvider,
    FederatedIdentity,
    UserFederationLink
)


@admin.register(Federation)
class FederationAdmin(admin.ModelAdmin):
    list_display = ('name', 'active', 'created_at', 'logo_preview')
    search_fields = ('name',)
    list_filter = ('active',)
    readonly_fields = ('logo_preview',)

    def logo_preview(self, obj):
        if obj.logo:
            return format_html('<img src="{}" style="height: 50px;" />', obj.logo.url)
        return "-"
    logo_preview.short_description = "Logo"


@admin.register(AuthRSAKeyPair)
class RSAKeyPairAdmin(admin.ModelAdmin):
    list_display = ('key_id', 'issuer', 'active', 'created_at')
    search_fields = ('key_id', 'issuer')
    list_filter = ('active',)


@admin.register(FederatedIdentity)
class FederatedIdentityAdmin(admin.ModelAdmin):
    list_display = ('subject', 'issuer', 'email', 'name', 'last_seen', 'federation')
    search_fields = ('subject', 'email', 'name')
    list_filter = ('issuer', 'federation')


@admin.register(UserFederationLink)
class UserFederationLinkAdmin(admin.ModelAdmin):
    list_display = ('user', 'federated_user', 'linked_at', 'active')
    search_fields = ('user__username', 'federated_user__subject')
    list_filter = ('active',)


@admin.register(LocalIdentityProvider)
class LocalIdentityProviderAdmin(admin.ModelAdmin):
    list_display = ('name', 'issuer_url', 'audience', 'contact_email', 'active', 'created_at', 'federation')
    search_fields = ('name', 'issuer_url', 'audience', 'contact_email')
    list_filter = ('active', 'federation')


@admin.register(ExternalIdentityProvider)
class ExternalIdentityProviderAdmin(admin.ModelAdmin):
    list_display = ('name', 'issuer_url', 'audience', 'jwks_url', 'trusted', 'last_verified', 'federation')
    search_fields = ('name', 'issuer_url', 'audience', 'jwks_url')
    list_filter = ('trusted', 'federation')
