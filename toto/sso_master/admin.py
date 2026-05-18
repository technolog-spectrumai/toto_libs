import json
from django.contrib import admin, messages
from django.http import HttpResponse
from toto.sso_core.manifest import ConnectionBundle
from .models import SSOAccessToken, SSOAuthorizationCode, SSORelyingParty, SSOSigningKey, SSOSubject


@admin.action(description="⬇ Generate connection bundle (rotates secret)")
def generate_connection_bundle(modeladmin, request, queryset):
    if queryset.count() != 1:
        modeladmin.message_user(request, "Select exactly one relying party.", level=messages.ERROR)
        return
    rp = queryset.first()
    new_secret = rp.set_client_secret()
    rp.save(update_fields=["client_secret_hash"])

    portal_url = request.build_absolute_uri("/").rstrip("/")
    bundle = ConnectionBundle(
        schema_version=1,
        bundle_type="oidc_connection_bundle",
        label=rp.name,
        portal_url=portal_url,
        client_id=rp.client_id,
        client_secret=new_secret,
        scopes=rp.allowed_scopes,
    )
    response = HttpResponse(bundle.to_json(), content_type="application/json")
    response["Content-Disposition"] = f'attachment; filename="{rp.client_id}-connection.json"'
    return response


@admin.register(SSORelyingParty)
class SSORelyingPartyAdmin(admin.ModelAdmin):
    list_display = ["name", "client_id", "client_type", "active", "trusted", "created_at"]
    list_filter = ["client_type", "active", "trusted"]
    search_fields = ["name", "client_id"]
    readonly_fields = ["created_at", "updated_at"]
    actions = [generate_connection_bundle]

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        from django.urls import reverse as _reverse
        extra_context["test_login_url"] = _reverse("sso:admin_test_login", args=[object_id])
        return super().change_view(request, object_id, form_url, extra_context)


@admin.register(SSOSigningKey)
class SSOSigningKeyAdmin(admin.ModelAdmin):
    list_display = ["key_id", "algorithm", "is_active", "created_at"]
    list_filter = ["is_active", "algorithm"]
    readonly_fields = ["key_id", "algorithm", "public_key_pem", "created_at"]
    def has_add_permission(self, request):
        return False


@admin.register(SSOSubject)
class SSOSubjectAdmin(admin.ModelAdmin):
    list_display = ["user", "subject", "created_at"]
    search_fields = ["user__username", "user__email", "subject"]
    readonly_fields = ["subject", "created_at"]


@admin.register(SSOAuthorizationCode)
class SSOAuthorizationCodeAdmin(admin.ModelAdmin):
    list_display = ["client", "user", "created_at", "expires_at", "used_at"]
    list_filter = ["client", "created_at"]
    search_fields = ["user__username"]
    readonly_fields = ["code", "created_at", "expires_at", "used_at"]


@admin.register(SSOAccessToken)
class SSOAccessTokenAdmin(admin.ModelAdmin):
    list_display = ["client", "user", "scope", "created_at", "expires_at", "revoked_at"]
    list_filter = ["client", "created_at"]
    readonly_fields = ["token", "created_at", "expires_at", "revoked_at"]
