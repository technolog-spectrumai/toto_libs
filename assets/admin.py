from django.contrib import admin
from django import forms
from django_json_widget.widgets import JSONEditorWidget

from .models import (
    AssetType,
    Asset,
    IdentityProfile,
    FractionalOwnership
)


# ---------------------------------------------------------
#  CUSTOM FORMS FOR JSON FIELDS
# ---------------------------------------------------------

class AssetAdminForm(forms.ModelForm):
    metadata = forms.JSONField(widget=JSONEditorWidget, required=False)

    class Meta:
        model = Asset
        fields = "__all__"


class IdentityProfileAdminForm(forms.ModelForm):
    metadata = forms.JSONField(widget=JSONEditorWidget, required=False)

    class Meta:
        model = IdentityProfile
        fields = "__all__"


# ---------------------------------------------------------
#  INLINE: Fractional Ownership inside Asset
# ---------------------------------------------------------

class FractionalOwnershipInline(admin.TabularInline):
    model = FractionalOwnership
    extra = 1
    autocomplete_fields = ["owner"]
    fields = ["owner", "percentage"]
    show_change_link = True


# ---------------------------------------------------------
#  ASSET ADMIN
# ---------------------------------------------------------

@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    form = AssetAdminForm

    list_display = [
        "name",
        "asset_type",
        "serial_number",
        "purchase_date",
        "purchase_price",
        "amount",
        "is_active",
    ]

    list_filter = [
        "asset_type",
        "is_active",
        "purchase_date",
    ]

    search_fields = [
        "name",
        "serial_number",
        "description",
    ]

    autocomplete_fields = ["asset_type", "assigned_to", "location"]
    inlines = [FractionalOwnershipInline]


# ---------------------------------------------------------
#  ASSET TYPE ADMIN
# ---------------------------------------------------------

@admin.register(AssetType)
class AssetTypeAdmin(admin.ModelAdmin):
    list_display = ["name", "description"]
    search_fields = ["name"]


# ---------------------------------------------------------
#  IDENTITY PROFILE ADMIN
# ---------------------------------------------------------

@admin.register(IdentityProfile)
class IdentityProfileAdmin(admin.ModelAdmin):
    form = IdentityProfileAdminForm

    list_display = [
        "name",
        "profile_type",
        "email",
        "phone",
        "is_verified",
        "verified_at",
    ]

    list_filter = [
        "profile_type",
        "is_verified",
    ]

    search_fields = [
        "name",
        "email",
        "phone",
        "registration_number"
    ]

    autocomplete_fields = ["member", "community", "address", "verified_by"]


# ---------------------------------------------------------
#  FRACTIONAL OWNERSHIP ADMIN
# ---------------------------------------------------------

@admin.register(FractionalOwnership)
class FractionalOwnershipAdmin(admin.ModelAdmin):
    list_display = [
        "asset",
        "owner",
        "percentage",
        "created_at",
    ]

    list_filter = ["asset"]
    search_fields = ["asset__name", "owner__name"]
    autocomplete_fields = ["asset", "owner"]
