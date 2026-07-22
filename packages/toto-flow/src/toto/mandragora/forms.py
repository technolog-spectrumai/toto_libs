from django import forms

from toto.vault.models import Bucket
from toto.verbena.forms import apply_oya_field_styles

from .models import Notebook


class NotebookForm(forms.ModelForm):
    class Meta:
        model = Notebook
        fields = ["title", "slug", "bucket"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Notebook title"}),
            "slug": forms.TextInput(attrs={"placeholder": "optional-custom-url"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False
        self.fields["bucket"].required = False
        self.fields["bucket"].queryset = Bucket.objects.order_by("name")
        self.fields["bucket"].empty_label = "— No bucket attached —"
        apply_oya_field_styles(self.fields)
