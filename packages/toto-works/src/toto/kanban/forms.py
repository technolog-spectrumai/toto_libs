from django import forms

from toto.kanban.models import Task, Mission, Column, Sprint, Practitioner


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
                commitments__project=project, is_active=True
            ).select_related("person").distinct()
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


