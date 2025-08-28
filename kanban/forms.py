from django import forms
from kanban.models import Task
from django.contrib.auth.models import User

class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = [
            "title", "description", "assignee",
            "due_date",
        ]
        widgets = {
            "title": forms.TextInput(attrs={
                "class": "w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300",
                "x-bind:class": "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'",
                "placeholder": "Task title"
            }),
            "description": forms.Textarea(attrs={
                "class": "w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300",
                "x-bind:class": "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'",
                "placeholder": "Task description",
                "rows": 4
            }),
            "assignee": forms.Select(attrs={
                "class": "w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300",
                "x-bind:class": "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'"
            }),
            "due_date": forms.DateInput(attrs={
                "type": "date",
                "class": "w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300",
                "x-bind:class": "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'"
            })
        }
