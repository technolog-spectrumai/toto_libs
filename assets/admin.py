from django.contrib import admin
from django import forms
from django_json_widget.widgets import JSONEditorWidget

from .models import (
    AssetType,
    Asset,
    FractionalOwnership,
    AssetImage
)


# ---------------------------------------------------------
#  CUSTOM FORMS FOR JSON FIELDS
# ---------------------------------------------------------

class AssetAdminForm(forms.ModelForm):
    metadata = forms.JSONField(widget=JSONEditorWidget, required=False)

    class Meta:
        model = Asset
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

class AssetImageInline(admin.TabularInline):
    model = AssetImage
    extra = 1
    fields = ["image", "caption", "order"]
    ordering = ["order"]


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
    list_filter = ["asset_type", "is_active", "purchase_date"]
    search_fields = ["name", "serial_number", "description"]
    autocomplete_fields = ["asset_type", "assigned_to", "location"]
    inlines = [AssetImageInline, FractionalOwnershipInline]



# ---------------------------------------------------------
#  ASSET TYPE ADMIN
# ---------------------------------------------------------

@admin.register(AssetType)
class AssetTypeAdmin(admin.ModelAdmin):
    list_display = ["name", "description"]
    search_fields = ["name"]



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
