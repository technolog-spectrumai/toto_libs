from django.contrib import admin
from django import forms
from django.utils.html import mark_safe
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
# FederatedIdentity Admin
# ---------------------------

@admin.register(FederatedIdentity)
class FederatedIdentityAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "federation",
        "user",
        "rsa_keypair",
        "created_at",
        "qr_preview",
    )
    list_filter = ("federation", "user", "created_at")
    search_fields = ("name", "id", "user__username", "user__email")
    readonly_fields = ("created_at", "qr_code")

    autocomplete_fields = ["federation", "user", "rsa_keypair"]

    def qr_preview(self, obj):
        return obj.qr_code()

    qr_preview.short_description = "QR Code"
    qr_preview.allow_tags = True


# ---------------------------
# IdentityProfile Admin Form
# ---------------------------

class IdentityProfileAdminForm(forms.ModelForm):
    metadata = forms.JSONField(widget=JSONEditorWidget, required=False)

    class Meta:
        model = IdentityProfile
        fields = "__all__"


# ---------------------------
# IdentityProfile Admin
# ---------------------------

@admin.register(IdentityProfile)
class IdentityProfileAdmin(admin.ModelAdmin):
    form = IdentityProfileAdminForm

    list_display = [
        "legal_name",
        "profile_type",
        "registration_number",
        "is_verified",
        "verified_at",
    ]

    list_filter = [
        "profile_type",
        "is_verified",
    ]

    search_fields = [
        "legal_name",
        "registration_number",
    ]

    autocomplete_fields = [
        "verified_by",
    ]

    readonly_fields = [
        "created_at",
        "verified_at",
    ]
