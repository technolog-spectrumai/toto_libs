from django import forms

from toto.inventory.models import (
    InventorySite,
    ObjectType,
    RealWorldObject,
    StorageLocation,
)


class ObjectTypeForm(forms.ModelForm):
    class Meta:
        model = ObjectType
        fields = [
            "name",
            "category",
            "is_mobile",
            "description",
            "metadata",
        ]


class RealWorldObjectForm(forms.ModelForm):
    class Meta:
        model = RealWorldObject
        fields = [
            "name",
            "object_type",
            "description",
            "owner",
            "custodian",
            "verified_by",
            "location",
            "quantity",
            "unit",
            "estimated_value",
            "estimated_value_currency",
            "valuation_date",
            "verified_at",
            "metadata",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "valuation_date": forms.DateInput(attrs={"type": "date"}),
            "verified_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }


class InventorySiteForm(forms.ModelForm):
    class Meta:
        model = InventorySite
        fields = [
            "name",
            "site_type",
            "operator",
            "address",
            "is_active",
            "is_virtual",
            "metadata",
        ]


class StorageLocationForm(forms.ModelForm):
    class Meta:
        model = StorageLocation
        fields = [
            "site",
            "name",
            "code",
            "location_type",
            "is_active",
            "metadata",
        ]


class StorageLocationForSiteForm(forms.ModelForm):
    class Meta:
        model = StorageLocation
        fields = [
            "name",
            "code",
            "location_type",
            "is_active",
            "metadata",
        ]
