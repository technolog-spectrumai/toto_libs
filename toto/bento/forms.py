from django import forms
from .models import Category, IdeaBox, IdeaLink

FIELD_CLASS = (
    "w-full rounded-lg border px-3 py-2 text-sm outline-none "
    "shadow-inner transition focus:ring-2 focus:ring-current/20"
)

FIELD_THEME_CLASS = (
    "darkMode "
    "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark placeholder:text-text-main-dark/45' "
    ": 'border-accent-2 bg-primary-bg-light text-text-main-light placeholder:text-text-main-light/45'"
)

class IdeaBoxForm(forms.ModelForm):
    class Meta:
        model = IdeaBox
        fields = [
            "label",
            "category",
            "properties",
        ]
        labels = {
            "properties": "Metadata (JSON)",
        }
        widgets = {
            "label": forms.TextInput(attrs={"placeholder": "Short label, optional"}),
            "category": forms.Select(),
            "properties": forms.Textarea(attrs={"rows": 14, "placeholder": (
                '{\n'
                '  "body": "Main idea / description",\n'
                '  "source_title": "",\n'
                '  "source_url": "",\n'
                '  "source_type": "",\n'
                '  "quote": "",\n'
                '  "rating": 4\n'
                '}'
            )}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", FIELD_CLASS)
            field.widget.attrs.setdefault("x-bind:class", FIELD_THEME_CLASS)


class IdeaLinkForm(forms.ModelForm):
    class Meta:
        model = IdeaLink
        fields = ["from_box", "label", "to_box", "properties"]
        labels = {
            "properties": "Metadata (JSON)",
        }
        widgets = {
            "label": forms.TextInput(attrs={"placeholder": "about, supports, contradicts, expands..."}),
            "properties": forms.Textarea(attrs={"rows": 5, "placeholder": '{\n  "strength": 0.8\n}'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", FIELD_CLASS)
            field.widget.attrs.setdefault("x-bind:class", FIELD_THEME_CLASS)


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "slug", "description"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Category name"}),
            "slug": forms.TextInput(attrs={"placeholder": "unique-category-slug"}),
            "description": forms.Textarea(attrs={"rows": 5, "placeholder": "Optional description"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", FIELD_CLASS)
            field.widget.attrs.setdefault("x-bind:class", FIELD_THEME_CLASS)