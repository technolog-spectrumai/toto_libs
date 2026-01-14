from django import forms
from django.contrib import admin
from django_json_widget.widgets import JSONEditorWidget
from finance.models import Asset, AssetType


# 📝 Asset Admin Form with JSON editor
class AssetAdminForm(forms.ModelForm):
    description = forms.CharField(widget=forms.Textarea, required=False)
    metadata = forms.JSONField(widget=JSONEditorWidget, required=False)

    class Meta:
        model = Asset
        fields = "__all__"


@admin.register(AssetType)
class AssetTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "created_at")
    search_fields = ("name",)
    ordering = ("name",)


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    form = AssetAdminForm
    list_display = (
        "name",
        "asset_type",
        "serial_number",
        "assigned_to",
        "location",
        "is_active",
        "purchase_date",
        "purchase_price",
        "created_at",
    )
    list_filter = ("asset_type", "is_active", "purchase_date")
    search_fields = ("name", "serial_number", "assigned_to__username", "location__city")
    autocomplete_fields = ("asset_type", "assigned_to", "location")
    readonly_fields = ("created_at",)
    ordering = ("name",)

    fieldsets = (
        (None, {
            "fields": ("name", "asset_type", "description", "metadata")
        }),
        ("Identification", {
            "fields": ("serial_number", "location")
        }),
        ("Ownership", {
            "fields": ("assigned_to", "is_active")
        }),
        ("Purchase Info", {
            "fields": ("purchase_date", "purchase_price")
        }),
        ("Timestamps", {
            "fields": ("created_at",)
        }),
    )
