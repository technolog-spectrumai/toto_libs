from django import forms

from django.utils import timezone

from toto.events.models import ScheduledEvent
from toto.people.models import Person
from toto.kanban.models import (
    Task, TaskRelation, Mission, Campaign, Sprint, Practitioner,
    SYMMETRIC_RELATIONS, visible_missions_for,
)


def _linkable_events(instance=None):
    """Events worth offering in a link dropdown: upcoming ones, plus whatever
    is already linked — or editing an old object fails validation on a field
    the user never touched. Deliberately not "unlinked only": one event may
    legitimately anchor several missions and tasks.
    """
    qs = ScheduledEvent.objects.filter(end_time__gte=timezone.now())
    if instance is not None and getattr(instance, "calendar_event_id", None):
        qs = qs | ScheduledEvent.objects.filter(pk=instance.calendar_event_id)
    return qs.order_by("start_time")


class TaskCreateForm(forms.ModelForm):

    def __init__(self, *args, project=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)

        if project:
            missions = Mission.objects.filter(campaign__project=project)
            if user is not None:
                # Without this the dropdown lists private missions' titles to
                # every project member.
                missions = visible_missions_for(user, missions)
            self.fields["mission"].queryset = missions
            self.fields["sprint"].queryset = Sprint.objects.filter(
                project=project
            )
            practitioners = Practitioner.objects.filter(
                commitments__project=project, is_active=True
            ).select_related("person").distinct()
            self.fields["assignee"].queryset = practitioners
            self.fields["reviewer"].queryset = practitioners

        self.fields["calendar_event"].queryset = _linkable_events(self.instance)

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
            "location",
            "calendar_event",
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
            "location": forms.Select(attrs={"class": _input, "x-bind:class": _dark}),
            "calendar_event": forms.Select(attrs={"class": _input, "x-bind:class": _dark}),
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


class MissionForm(forms.ModelForm):
    """Create or edit a mission — the fields admin used to gate.

    Zone-containment errors surface on the zone field automatically:
    ModelForm validation runs Mission.clean via full_clean.
    """

    def __init__(self, *args, project=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project

        if project:
            self.fields["campaign"].queryset = Campaign.objects.filter(project=project)
        self.fields["visible_to"].queryset = Person.objects.order_by("display_name")
        self.fields["calendar_event"].queryset = _linkable_events(self.instance)

    class Meta:
        model = Mission
        fields = [
            "title",
            "description",
            "campaign",
            "urgency",
            "impact",
            "owner",
            "location",
            "route",
            "zone",
            "visibility",
            "visible_to",
            "calendar_event",
        ]

        _input = "w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300"
        _dark = "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'"

        widgets = {
            "title": forms.TextInput(attrs={
                "class": _input,
                "x-bind:class": _dark,
                "placeholder": "Mission title",
            }),
            "description": forms.Textarea(attrs={
                "class": _input,
                "x-bind:class": _dark,
                "rows": 4,
            }),
            "campaign": forms.Select(attrs={"class": _input, "x-bind:class": _dark}),
            "urgency": forms.Select(attrs={"class": _input, "x-bind:class": _dark}),
            "impact": forms.Select(attrs={"class": _input, "x-bind:class": _dark}),
            "owner": forms.Select(attrs={"class": _input, "x-bind:class": _dark}),
            "location": forms.Select(attrs={"class": _input, "x-bind:class": _dark}),
            "route": forms.Select(attrs={"class": _input, "x-bind:class": _dark}),
            "zone": forms.Select(attrs={"class": _input, "x-bind:class": _dark}),
            "visibility": forms.Select(attrs={"class": _input, "x-bind:class": _dark}),
            "visible_to": forms.SelectMultiple(attrs={
                "class": _input,
                "x-bind:class": _dark,
                "size": 6,
            }),
            "calendar_event": forms.Select(attrs={"class": _input, "x-bind:class": _dark}),
        }


class LinkedEventCreateForm(forms.ModelForm):
    """The minimal event a mission or task can schedule itself onto.

    end <= start is refused for free: ModelForm validation runs
    EventBase.clean.
    """

    class Meta:
        model = ScheduledEvent
        fields = ["title", "start_time", "end_time"]

        _input = "w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300"
        _dark = "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'"

        widgets = {
            "title": forms.TextInput(attrs={"class": _input, "x-bind:class": _dark}),
            "start_time": forms.DateTimeInput(
                attrs={"type": "datetime-local", "class": _input, "x-bind:class": _dark},
                format="%Y-%m-%dT%H:%M",
            ),
            "end_time": forms.DateTimeInput(
                attrs={"type": "datetime-local", "class": _input, "x-bind:class": _dark},
                format="%Y-%m-%dT%H:%M",
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("start_time", "end_time"):
            self.fields[name].input_formats = ["%Y-%m-%dT%H:%M"]
