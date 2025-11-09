from django.contrib import admin
from .models import Federation, FederatedIdentity, Challenge
from django.utils.html import format_html


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
    readonly_fields = ("created_at",)


@admin.register(Challenge)
class ChallengeAdmin(admin.ModelAdmin):
    list_display = (
        "identity",
        "masked_nonce",
        "issued_at",
        "expires_at",
        "status_display",
    )
    list_filter = ("verified", "issued_at", "expires_at")
    search_fields = ("nonce", "identity__id", "identity__name")
    readonly_fields = ("issued_at", "expires_at", "verified", "masked_nonce")

    def masked_nonce(self, obj):
        """
        Show a masked version of the nonce so it isn't exposed in plain text.
        """
        return "*" * len(obj.nonce) if obj.nonce else ""
    masked_nonce.short_description = "Nonce (masked)"

    def status_display(self, obj):
        """
        Color-coded status for quick visual feedback.
        """
        if obj.verified:
            return format_html('<span style="color:green;">Verified ✅</span>')
        elif obj.expires_at and obj.expires_at < obj.issued_at:
            return format_html('<span style="color:red;">Expired ❌</span>')
        return format_html('<span style="color:orange;">Pending ⏳</span>')
    status_display.short_description = "Status"


@admin.register(RefreshToken)
class RefreshTokenAdmin(admin.ModelAdmin):
    list_display = (
        "identity",
        "short_token",
        "issued_at",
        "expires_at",
        "revoked",
        "status_display",
    )
    list_filter = ("revoked", "issued_at", "expires_at")
    search_fields = ("token", "identity__name", "identity__id")
    readonly_fields = ("issued_at", "short_token")

    actions = ["revoke_tokens"]

    def short_token(self, obj):
        """
        Show a shortened version of the token for readability.
        """
        return obj.token[:12] + "..." if obj.token else ""
    short_token.short_description = "Token (short)"

    def status_display(self, obj):
        """
        Color-coded status for quick visual feedback.
        """
        if obj.revoked:
            return format_html('<span style="color:red;">Revoked ❌</span>')
        elif obj.is_expired():
            return format_html('<span style="color:orange;">Expired ⏳</span>')
        return format_html('<span style="color:green;">Active ✅</span>')
    status_display.short_description = "Status"

    def revoke_tokens(self, request, queryset):
        """
        Admin action to revoke selected tokens.
        """
        count = queryset.update(revoked=True)
        self.message_user(request, f"{count} token(s) revoked.")
    revoke_tokens.short_description = "Revoke selected tokens"
