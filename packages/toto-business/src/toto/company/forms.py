"""Forms for the company app.

Revived from irena's `toto.company.forms` and `toto.departments.forms`, merged.
The department-decision form is deferred to Stage 2, where the ledger it writes
to exists.
"""

from __future__ import annotations

from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.utils.text import slugify

from toto.company.models import (
    ActionKind,
    Company,
    CompanyAction,
    CompanyForum,
    CompanyForm as CompanyFormChoices,
    CompanyMembership,
    Department,
    DepartmentMembership,
    Party,
    ShareClass,
)
from toto.company.services.ownership import record_shareholding


FIELD_CLASS = "w-full rounded-lg border bg-inherit px-3 py-2 text-sm text-inherit outline-none"


def _style(form):
    for field in form.fields.values():
        if not isinstance(field.widget, forms.CheckboxInput):
            field.widget.attrs.setdefault("class", FIELD_CLASS)


class CompanyDetailsForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = [
            "name",
            "form",
            "legal_form_label",
            "registry_no",
            "tax_no",
            "statistical_no",
            "seat",
            "share_capital",
            "capital_currency",
            "logo",
            "statute_text",
            "community_ref",
            "constitutive_document_ref",
            "note",
        ]
        widgets = {
            "form": forms.Select(choices=CompanyFormChoices.choices),
            "seat": forms.Textarea(attrs={"rows": 2}),
            "statute_text": forms.Textarea(attrs={"rows": 6}),
            "note": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


class CompanyForumForm(forms.ModelForm):
    """Which forum room is this company's — picked from a list.

    A DROPDOWN OVER A SLUG, which is the awkward-looking part and the correct
    one. `CompanyForum.channel_slug` is a slug rather than a ForeignKey
    because toto-business may not depend on toto-chat: an FK *string* counts
    as a hard package edge and `check_package_graph.py` refuses it. So a
    `ModelChoiceField` over ForumChannel is out — it would reintroduce
    precisely the import the slug design avoids.

    What is possible, and what this does, is to POPULATE the choices at render
    time from whatever rooms exist, while still storing a slug. The app is
    asked for through the registry, never imported, so a host without chat
    gets a form with no rooms to choose rather than an ImportError.

    The empty choice is how a link is REMOVED — the view deletes the row when
    the slug comes back blank, because "no room" is the absence of the row
    rather than a row holding "".
    """

    class Meta:
        model = CompanyForum
        fields = ["channel_slug"]
        labels = {"channel_slug": "Forum room"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields["channel_slug"]
        field.required = False

        rooms = self._rooms()
        if rooms is None:
            # No forum on this host. Left as a text input rather than an empty
            # dropdown: an empty <select> looks like a bug, and a host that
            # installs chat later should not need this row retyped.
            field.help_text = ("This host has no forum installed, so there "
                               "are no rooms to choose from.")
        else:
            choices = [("", "— no room —")] + [(r.slug, r.name) for r in rooms]
            # A stored slug whose room has since been deleted would otherwise
            # vanish from the dropdown and be silently cleared on the next
            # save. Keep it visible, and say what it is.
            current = (self.instance.channel_slug
                       if self.instance and self.instance.pk else "")
            if current and current not in {slug for slug, _label in choices}:
                choices.append((current, f"{current} (no such room)"))
            field.widget = forms.Select(choices=choices)
            field.help_text = "Leave empty to remove the link."
        _style(self)

    @staticmethod
    def _rooms():
        """Every room, or None when this host has no forum.

        Through the app registry rather than a module-level import — see the
        class docstring. `get_model` raises on a host without chat, which is
        the case this returns None for.
        """
        from django.apps import apps as django_apps

        if not django_apps.is_installed("toto.forum"):
            return None
        try:
            model = django_apps.get_model("forum", "ForumChannel")
            return list(model.objects.order_by("name"))
        except Exception:  # noqa: BLE001 — a missing app is not an error here
            return None


class ShareClassForm(forms.ModelForm):
    class Meta:
        model = ShareClass
        fields = ["name", "slug", "votes_per_unit", "nominal_value", "note"]
        widgets = {"note": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, company, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company
        self.fields["slug"].required = False
        _style(self)

    def clean_slug(self):
        slug = self.cleaned_data.get("slug") or slugify(self.cleaned_data.get("name") or "")
        if not slug:
            raise ValidationError("Share class needs a name or slug.")
        if ShareClass.objects.filter(company=self.company, slug=slug).exists():
            raise ValidationError("This share class slug is already used for this company.")
        return slug

    def save(self, commit=True):
        share_class = super().save(commit=False)
        share_class.company = self.company
        if commit:
            share_class.save()
        return share_class


class ShareholderStructureForm(forms.Form):
    """Set one shareholder's live position exactly, through an append-only event."""

    party_name = forms.CharField(max_length=200, label="Shareholder")
    is_organisation = forms.BooleanField(required=False, label="Organisation")
    registry_no = forms.CharField(max_length=64, required=False, label="Registry number")
    tax_no = forms.CharField(max_length=32, required=False, label="Tax number")
    share_class = forms.ModelChoiceField(queryset=ShareClass.objects.none(), label="Share class")
    units = forms.DecimalField(max_digits=24, decimal_places=6, min_value=Decimal("0.000001"))
    evidence_ref = forms.CharField(max_length=255, required=False, label="Evidence reference")
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, company, recorded_by=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company
        self.recorded_by = recorded_by
        self.fields["share_class"].queryset = company.share_classes.filter(active=True).order_by("slug")
        _style(self)

    def clean_party_name(self):
        return self.cleaned_data["party_name"].strip()

    def save(self):
        party, _ = Party.objects.get_or_create(
            company=self.company,
            name=self.cleaned_data["party_name"],
            defaults={
                "is_organisation": self.cleaned_data["is_organisation"],
                "registry_no": self.cleaned_data["registry_no"],
                "tax_no": self.cleaned_data["tax_no"],
            },
        )
        updates = []
        for field in ("is_organisation", "registry_no", "tax_no"):
            value = self.cleaned_data[field]
            if getattr(party, field) != value:
                setattr(party, field, value)
                updates.append(field)
        if updates:
            updates.append("updated_at")
            party.save(update_fields=updates)

        return record_shareholding(
            company=self.company,
            party=party,
            share_class=self.cleaned_data["share_class"],
            units=self.cleaned_data["units"],
            recorded_by=self.recorded_by,
            authority_reference="Shareholder structure form",
            evidence_ref=self.cleaned_data["evidence_ref"],
            note=self.cleaned_data["note"],
        )


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = (
            "name",
            "slug",
            "description",
            "parent",
            "head",
            "email",
            "phone",
            "address",
            "active",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "address": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, company, **kwargs):
        self.company = company
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False
        self.fields["parent"].queryset = (
            Department.objects.filter(company=company)
            .exclude(pk=self.instance.pk)
            .order_by("name")
        )
        self.fields["head"].queryset = Party.objects.filter(
            company=company, active=True,
        ).order_by("name")
        _style(self)

    def clean_slug(self):
        slug = self.cleaned_data.get("slug") or slugify(self.cleaned_data.get("name") or "")
        if not slug:
            raise ValidationError("A department needs a name or a slug.")
        clash = Department.objects.filter(company=self.company, slug=slug)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise ValidationError("This department slug is already used for this company.")
        return slug

    def save(self, commit=True):
        department = super().save(commit=False)
        department.company = self.company
        if commit:
            department.save()
        return department


class DepartmentMembershipForm(forms.ModelForm):
    class Meta:
        model = DepartmentMembership
        fields = ("party", "title", "is_leadership", "can_record_decisions", "joined_on", "note")
        widgets = {
            "joined_on": forms.DateInput(attrs={"type": "date"}),
            "note": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, department, **kwargs):
        self.department = department
        super().__init__(*args, **kwargs)
        self.fields["party"].queryset = Party.objects.filter(
            company=department.company, active=True,
        ).order_by("name")
        _style(self)

    def save(self, commit=True):
        membership = super().save(commit=False)
        membership.department = self.department
        if commit:
            membership.save()
        return membership


class CompanyMembershipForm(forms.ModelForm):
    """Link an existing Person to a company. `toto.people` owns the Person."""

    class Meta:
        model = CompanyMembership
        fields = ("person", "job_title", "primary_department", "joined_on", "note")
        widgets = {
            "joined_on": forms.DateInput(attrs={"type": "date"}),
            "note": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, company, **kwargs):
        self.company = company
        super().__init__(*args, **kwargs)
        from toto.people.models import Person

        self.fields["person"].queryset = Person.objects.order_by("display_name")
        self.fields["primary_department"].queryset = Department.objects.filter(
            company=company, active=True,
        ).order_by("name")
        self.fields["primary_department"].required = False
        _style(self)

    def _post_clean(self):
        # The company BEFORE model validation, not in save().
        #
        # `CompanyMembership.clean` compares `primary_department.company_id`
        # to `self.company_id`, and Django runs `instance.full_clean()` here in
        # `_post_clean` — which happens before `save()` ever runs. Assigning
        # the company in `save()` therefore left company_id None at the moment
        # the check ran, so every department the dropdown offered was rejected
        # as belonging to a different company, and no member could be added at
        # all. The queryset above is what actually restricts the choice; this
        # just lets the model's own guard see the same instance the save will.
        self.instance.company = self.company
        super()._post_clean()

    def clean(self):
        cleaned = super().clean()
        person = cleaned.get("person")
        if person and CompanyMembership.objects.filter(
            company=self.company, person=person, active=True,
        ).exists():
            raise ValidationError("This person is already an active member of the company.")
        return cleaned

    def save(self, commit=True):
        membership = super().save(commit=False)
        membership.company = self.company
        if commit:
            membership.save()
        return membership


class CompanyActionForm(forms.ModelForm):
    """A draft. Recording it is a separate, deliberate act."""

    class Meta:
        model = CompanyAction
        fields = ("title", "kind", "body")
        widgets = {
            "body": forms.Textarea(attrs={"rows": 8}),
            "kind": forms.Select(choices=ActionKind.choices),
        }

    def __init__(self, *args, company, **kwargs):
        self.company = company
        super().__init__(*args, **kwargs)
        _style(self)

    def save(self, commit=True):
        action = super().save(commit=False)
        action.company = self.company
        if commit:
            action.save()
        return action


class AddressPointForm(forms.ModelForm):
    """One mapped point: a postal-ish address plus coordinates.

    Coordinates are clicked on the map (or typed, or pasted from any map
    app) rather than geocoded server-side — the suite's geocoding helpers
    stay the upgrade path. Ranges are enforced here; the model keeps plain
    floats.
    """

    latitude = forms.FloatField(
        required=False, min_value=-90, max_value=90, label="Latitude",
        help_text="Decimal degrees, e.g. 52.2297.")
    longitude = forms.FloatField(
        required=False, min_value=-180, max_value=180, label="Longitude",
        help_text="Decimal degrees, e.g. 21.0122.")

    class Meta:
        from toto.locations.models import Address

        model = Address
        fields = ("country_name", "state_or_province_name", "locality_name",
                  "street", "building", "latitude", "longitude")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", FIELD_CLASS)

    def clean(self):
        data = super().clean()
        lat, lon = data.get("latitude"), data.get("longitude")
        if (lat is None) != (lon is None):
            raise ValidationError("Give both coordinates, or neither.")
        return data
