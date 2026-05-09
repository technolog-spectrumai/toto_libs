from django import forms
from toto.kanban.models import Task, Mission, Column, Sprint
from toto.socialhub.models import Person


class TaskCreateForm(forms.ModelForm):

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)

        # Filter fields based on project
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

    class Meta:
        model = Task
        fields = [
            "title",
            "description",
            "mission",
            "column",
            "sprint",
            "assignee",
            "due_date",
            "weight",
        ]

        widgets = {
            "title": forms.TextInput(attrs={
                "class": "w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300",
                "x-bind:class": "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'",
                "placeholder": "Task title",
            }),

            "description": forms.Textarea(attrs={
                "class": "w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300",
                "x-bind:class": "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'",
                "placeholder": "Describe the task (optional)",
                "rows": 4,
            }),

            "mission": forms.Select(attrs={
                "class": "w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300",
                "x-bind:class": "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'",
            }),

            "column": forms.Select(attrs={
                "class": "w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300",
                "x-bind:class": "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'",
            }),

            "sprint": forms.Select(attrs={
                "class": "w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300",
                "x-bind:class": "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'",
            }),

            "assignee": forms.Select(attrs={
                "class": "w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300",
                "x-bind:class": "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'",
            }),

            "due_date": forms.DateInput(attrs={
                "type": "date",
                "class": "w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300",
                "x-bind:class": "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'",
            }),

            "weight": forms.Select(attrs={
                "class": "w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300",
                "x-bind:class": "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'",
            }),
        }
