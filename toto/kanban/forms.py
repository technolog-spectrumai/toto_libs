from decimal import Decimal

from django import forms

from toto.kanban.models import Task, Mission, Column, Sprint, Practitioner, ProjectTokenization


class TaskCreateForm(forms.ModelForm):

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)

        if project:
            self.fields["mission"].queryset = Mission.objects.filter(
                campaign__project=project
            )
            self.fields["column"].queryset = Column.objects.filter(
                project=project
            )
            self.fields["sprint"].queryset = Sprint.objects.filter(
                project=project
            )
            practitioners = Practitioner.objects.filter(
                project=project, is_active=True
            ).select_related("person")
            self.fields["assignee"].queryset = practitioners
            self.fields["reviewer"].queryset = practitioners

    class Meta:
        model = Task
        fields = [
            "title",
            "description",
            "mission",
            "column",
            "sprint",
            "assignee",
            "reviewer",
            "due_date",
            "weight",
        ]

        _input = "w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300"
        _dark = "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'"

        widgets = {
            "title": forms.TextInput(attrs={
                "class": _input,
                "x-bind:class": _dark,
                "placeholder": "Task title",
            }),
            "description": forms.Textarea(attrs={
                "class": _input,
                "x-bind:class": _dark,
                "placeholder": "Describe the task (optional)",
                "rows": 4,
            }),
            "mission": forms.Select(attrs={"class": _input, "x-bind:class": _dark}),
            "column": forms.Select(attrs={"class": _input, "x-bind:class": _dark}),
            "sprint": forms.Select(attrs={"class": _input, "x-bind:class": _dark}),
            "assignee": forms.Select(attrs={"class": _input, "x-bind:class": _dark}),
            "reviewer": forms.Select(attrs={"class": _input, "x-bind:class": _dark}),
            "due_date": forms.DateInput(attrs={
                "type": "date",
                "class": _input,
                "x-bind:class": _dark,
            }),
            "weight": forms.Select(attrs={"class": _input, "x-bind:class": _dark}),
        }


class ProjectTokenizationCreateForm(forms.Form):
    asset_name = forms.CharField(max_length=255, label="Asset name")
    unit_name = forms.CharField(max_length=20, label="Unit name")
    decimals = forms.IntegerField(min_value=0, max_value=19, initial=0)
    total_supply = forms.DecimalField(max_digits=24, decimal_places=8, min_value=Decimal("0.00000001"))
    reserve_account = forms.ModelChoiceField(
        queryset=None,
        help_text="Initial supply is issued to this reserve account.",
    )
    supervisor = forms.ModelChoiceField(
        queryset=None,
        required=False,
        help_text="Person responsible for supervising this tokenization.",
    )
    is_currency = forms.BooleanField(required=False, label="Accepted as payment currency")
    backing_document = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Describe the legal or financial backing for this project token.",
    )
    minting_authority = forms.CharField(max_length=255, required=False)
    metadata = forms.JSONField(required=False, initial=dict, widget=forms.Textarea(attrs={"rows": 4}))

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project

        from toto.assets.models import Asset, LedgerAccount
        from toto.people.models import Person

        self.fields["reserve_account"].queryset = LedgerAccount.objects.filter(active=True).order_by("code")
        self.fields["supervisor"].queryset = Person.objects.order_by("display_name")

        _css = "w-full rounded-lg border px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-current/20 border-accent-2 bg-primary-bg-light text-text-main-light"
        for field in self.fields.values():
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} {_css}".strip()

    def clean_unit_name(self):
        from toto.assets.models import Asset
        unit_name = self.cleaned_data["unit_name"].strip().upper()
        if Asset.objects.filter(unit_name__iexact=unit_name).exists():
            raise forms.ValidationError("An asset with this unit name already exists.")
        return unit_name

    def clean(self):
        cleaned = super().clean()
        if self.project and ProjectTokenization.objects.filter(project=self.project).exists():
            raise forms.ValidationError("This project is already tokenized and cannot be tokenized again.")
        return cleaned


class ProjectTokenizationDefaultForm(forms.Form):
    from toto.kanban.models import ProjectTokenizationDefaultReason
    reason = forms.ChoiceField(choices=ProjectTokenizationDefaultReason.choices)
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Optional details for the default record.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _css = "w-full rounded-lg border px-3 py-2 text-sm outline-none transition focus:ring-2 focus:ring-current/20 border-accent-2 bg-primary-bg-light text-text-main-light"
        for field in self.fields.values():
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} {_css}".strip()
