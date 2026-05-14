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

CHECKBOX_CLASS = (
    "h-4 w-4 rounded"
)

CHECKBOX_THEME_CLASS = (
    "darkMode "
    "? 'border-accent-1 bg-primary-bg-dark' "
    ": 'border-accent-2 bg-primary-bg-light'"
)


class IdeaBoxForm(forms.ModelForm):
    class Meta:
        model = IdeaBox
        fields = [
            "title",
            "body",
            "is_concept",
            "source_title",
            "source_url",
            "source_type",
            "quote",
            "category",  # removed topic
            "properties",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Short title, optional"}),
            "body": forms.Textarea(attrs={"rows": 6, "placeholder": "Main idea / concept description"}),
            "source_title": forms.TextInput(attrs={"placeholder": "Book, article, movie, video title..."}),
            "source_url": forms.URLInput(attrs={"placeholder": "https://..."}),
            "source_type": forms.TextInput(attrs={"placeholder": "book, article, movie, video, podcast..."}),
            "quote": forms.Textarea(attrs={"rows": 4, "placeholder": "Optional quote"}),
            "category": forms.Select(),
            "properties": forms.Textarea(attrs={"rows": 6, "placeholder": '{\n  "rating": 4,\n  "status": "raw"\n}'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name == "is_concept":
                field.widget.attrs.setdefault("class", CHECKBOX_CLASS)
                field.widget.attrs.setdefault("x-bind:class", CHECKBOX_THEME_CLASS)
            else:
                field.widget.attrs.setdefault("class", FIELD_CLASS)
                field.widget.attrs.setdefault("x-bind:class", FIELD_THEME_CLASS)


class IdeaLinkForm(forms.ModelForm):
    class Meta:
        model = IdeaLink
        fields = ["from_box", "label", "to_box", "properties"]
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