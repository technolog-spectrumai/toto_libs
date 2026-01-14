from django.contrib import admin
from django import forms
from django.forms import JSONField
from django_json_widget.widgets import JSONEditorWidget

from .models import Federation, FederatedIdentity, IdentityProfile


# ---------------------------
# Federation Admin
# ---------------------------

@admin.register(Federation)
class FederationAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "active",
        "created_at",
        "platform",
        "location",
        "is_foreign",
    )

    list_filter = ("active", "platform", "location")

    search_fields = (
        "name",
        "slug",
        "description",
        "platform__name",
        "location__locality_name",
    )

    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at",)


# ---------------------------
# IdentityProfile Admin Form
# ---------------------------

class IdentityProfileAdminForm(forms.ModelForm):
    metadata = JSONField(widget=JSONEditorWidget, required=False)

    class Meta:
        model = IdentityProfile
        fields = "__all__"


# ---------------------------
# FederatedIdentity Inline
# ---------------------------

class FederatedIdentityInline(admin.StackedInline):
    model = FederatedIdentity
    extra = 0
    can_delete = False
    readonly_fields = ("id", "created_at")
    fields = ("id", "name", "user", "rsa_keypair", "created_at")


# ---------------------------
# IdentityProfile Admin
# ---------------------------

@admin.register(IdentityProfile)
class IdentityProfileAdmin(admin.ModelAdmin):
    form = IdentityProfileAdminForm
    inlines = [FederatedIdentityInline]

    list_display = [
        "legal_name",
        "profile_type",
        "registration_number",
        "is_verified",
        "member",
        "community",
        "federated_identity_display",
    ]

    list_filter = ["profile_type", "is_verified"]

    search_fields = [
        "legal_name",
        "registration_number",
        "member__display_name",
        "community__name",
    ]

    autocomplete_fields = ["member", "community", "verified_by"]

    readonly_fields = ["created_at", "verified_at"]

    fieldsets = (
        ("Identity", {
            "fields": (
                "profile_type",
                "legal_name",
                "registration_number",
                "registration_type",
            )
        }),
        ("Links", {
            "fields": ("member", "community")
        }),
        ("Verification", {
            "fields": ("is_verified", "verified_at", "verified_by")
        }),
        ("Metadata", {
            "fields": ("metadata",)
        }),
        ("System", {
            "fields": ("created_at",)
        }),
    )

    def federated_identity_display(self, obj):
        fi = getattr(obj, "federated_identity", None)
        if not fi:
            return "-"
        return f"{fi.id} ({fi.name or 'no name'})"

    federated_identity_display.short_description = "Federated Identity"


# ---------------------------
# FederatedIdentity Admin
# ---------------------------

@admin.register(FederatedIdentity)
class FederatedIdentityAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "profile",
        "user",
        "rsa_keypair",
        "created_at",
    )

    search_fields = (
        "id",
        "name",
        "profile__legal_name",
        "user__username",
    )

    readonly_fields = ("id", "created_at")

    ordering = ("-created_at",)
