from django import forms

from toto.kanban.models import (
    Task, TaskRelation, Mission, Sprint, Practitioner, SYMMETRIC_RELATIONS,
)


class TaskCreateForm(forms.ModelForm):

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)

        if project:
            self.fields["mission"].queryset = Mission.objects.filter(
                campaign__project=project
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




class TaskRelationForm(forms.ModelForm):
    """Link one task to another in the same campaign.

    The target dropdown is scoped to the campaign, so the same-campaign rule
    shows up as "there is nothing else to pick" rather than as an error after
    the fact. TaskRelation.clean still enforces it — the queryset is a
    convenience, not the guarantee.
    """

    def __init__(self, *args, from_task=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.from_task = from_task

        if from_task is not None:
            # Set on the instance, not just the form: ModelForm validates the
            # instance during _post_clean, and TaskRelation.clean bails out
            # early when it cannot see both endpoints. Without this the
            # same-campaign and cycle rules would only fire later, in save(),
            # where a ValidationError is a 500 rather than a form error.
            self.instance.from_task = from_task
            self.fields["to_task"].queryset = (
                Task.objects
                .filter(mission__campaign_id=from_task.mission.campaign_id)
                .exclude(pk=from_task.pk)
                .select_related("mission")
                .order_by("mission__title", "title")
            )

    class Meta:
        model = TaskRelation
        fields = ["relation_type", "to_task", "note"]

        _input = "w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300"
        _dark = "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'"

        widgets = {
            "relation_type": forms.Select(attrs={"class": _input, "x-bind:class": _dark}),
            "to_task": forms.Select(attrs={"class": _input, "x-bind:class": _dark}),
            "note": forms.TextInput(attrs={
                "class": _input,
                "x-bind:class": _dark,
                "placeholder": "Why (optional)",
            }),
        }

    def clean(self):
        cleaned = super().clean()
        to_task = cleaned.get("to_task")
        relation_type = cleaned.get("relation_type")

        # from_task is not a form field, so ModelForm excludes it from the
        # unique check and a duplicate would reach the database as an
        # IntegrityError. Ask here instead, in both directions for the
        # symmetric types whose stored direction is arbitrary.
        if self.from_task and to_task and relation_type:
            pairs = [(self.from_task.pk, to_task.pk)]
            if relation_type in SYMMETRIC_RELATIONS:
                pairs.append((to_task.pk, self.from_task.pk))
            exists = any(
                TaskRelation.objects
                .filter(from_task_id=a, to_task_id=b, relation_type=relation_type)
                .exclude(pk=self.instance.pk)
                .exists()
                for a, b in pairs
            )
            if exists:
                raise forms.ValidationError("These tasks are already linked that way.")

        return cleaned

    def save(self, commit=True):
        relation = super().save(commit=False)
        relation.from_task = self.from_task
        if commit:
            relation.save()
        return relation
