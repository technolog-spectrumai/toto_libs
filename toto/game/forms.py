from django import forms
from django.core.exceptions import ValidationError

from .engine import construction_required_work
from .models import Building, BuildingType, ConstructionProject, Province


class CreateProvinceForm(forms.ModelForm):
    class Meta:
        model = Province
        fields = [
            "name",
            "width",
            "height",
            "avg_elev",
            "avg_temp",
            "avg_rain",
            "wind_speed",
            "cell_count",
            "x",
            "y",
        ]


class RenameProvinceForm(forms.ModelForm):
    class Meta:
        model = Province
        fields = ["name"]


class QuickBuildForm(forms.Form):
    building_type = forms.ModelChoiceField(queryset=BuildingType.objects.none(), label="Building")
    target_level = forms.IntegerField(min_value=1, max_value=10, initial=1)
    priority = forms.ChoiceField(choices=Building._meta.get_field("priority").choices, initial="normal")

    def __init__(self, *args, province=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.province = province
        if province is not None:
            self.fields["building_type"].queryset = BuildingType.objects.filter(
                is_active=True,
                cell_cost__lte=province.free_cells,
            ).order_by("category", "name")

    def clean_building_type(self):
        building_type = self.cleaned_data["building_type"]
        if self.province and building_type.cell_cost > self.province.free_cells:
            raise ValidationError("This province does not have enough free cells for that building.")
        return building_type

    def create_project(self):
        target_level = self.cleaned_data["target_level"]
        return ConstructionProject.objects.create(
            province=self.province,
            building_type=self.cleaned_data["building_type"],
            target_level=target_level,
            priority=self.cleaned_data["priority"],
            required_work=construction_required_work(target_level),
        )


class BuildingControlForm(forms.ModelForm):
    class Meta:
        model = Building
        fields = ["status", "priority", "selected_recipe"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        building = self.instance
        if building and building.pk:
            self.fields["selected_recipe"].queryset = building.building_type.recipes.filter(is_active=True)
            self.fields["selected_recipe"].required = False
