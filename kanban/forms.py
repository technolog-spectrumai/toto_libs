from django import forms
from kanban.models import Task
from oya.models import Theme


class TaskForm(forms.ModelForm):
    def __init__(self, *args, theme: Theme = None, **kwargs):
        super().__init__(*args, **kwargs)

        # Fallback if no theme is passed
        colors = theme.theme.get("colors", {}) if theme else {}

        # Extract desired styles (optional, if you want to inline styles)
        bg_color = colors.get("primary-bg-light", "#f8f9fa")
        text_color = colors.get("text-main-light", "#212529")

        # Apply Tailwind + Alpine darkMode binding
        self.fields["title"].widget.attrs.update({
            "class": "w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300",
            "x-bind:class": "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'",
            "placeholder": "Task title"
        })
        self.fields["description"].widget.attrs.update({
            "class": "w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300",
            "x-bind:class": "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'",
            "placeholder": "Task description"
        })

    class Meta:
        model = Task
        fields = ["title", "description"]
