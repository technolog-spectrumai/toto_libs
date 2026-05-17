from django import forms

from toto.verbena.forms import apply_oya_field_styles

from .models import Notebook


class NotebookForm(forms.ModelForm):
    class Meta:
        model = Notebook
        fields = ["title", "slug"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Notebook title"}),
            "slug": forms.TextInput(attrs={"placeholder": "optional-custom-url"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False
        apply_oya_field_styles(self.fields)
