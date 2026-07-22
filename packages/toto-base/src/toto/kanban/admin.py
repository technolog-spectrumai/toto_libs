from datetime import timedelta
import json

from django import forms
from django.contrib import admin
from django.utils.timezone import now

from .models import (
    Project, Column, Task, Sprint, Mission, Campaign,
    DocumentationPage, DocumentationSection,
    Practitioner, ProjectCommitment,
)
from toto.core.batch import BatchAction
from toto.events.models import ScheduledEvent
from toto.verbena.admin import SectionInlineMixin, PageAdminMixin


class PrettyJSONTextarea(forms.Textarea):
    def format_value(self, value):
        if value in (None, ""):
            return ""
        if isinstance(value, str):
            return value
        return json.dumps(value, indent=2, ensure_ascii=False)


# ── Project ──────────────────────────────────────────────────────────────────

class ProjectCommitmentInline(admin.TabularInline):
    model = ProjectCommitment
    extra = 1
    fields = ("practitioner", "hours_per_day", "is_active", "start_date", "end_date")
    raw_id_fields = ("practitioner",)


class CampaignInline(admin.TabularInline):
    model = Campaign
    extra = 1
    fields = ("name", "start_date", "end_date", "owner")
    ordering = ("start_date",)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "project_lead", "commitment_count")
    search_fields = ("name", "description")
    inlines = [CampaignInline, ProjectCommitmentInline]

    def commitment_count(self, obj):
        return obj.commitments.filter(is_active=True).count()
    commitment_count.short_description = "Practitioners"


# ── Practitioner ──────────────────────────────────────────────────────────────

class PractitionerCommitmentInline(admin.TabularInline):
    model = ProjectCommitment
    extra = 0
    fields = ("project", "hours_per_day", "is_active", "start_date", "end_date")
    raw_id_fields = ("project",)


@admin.register(Practitioner)
class PractitionerAdmin(admin.ModelAdmin):
    list_display = ("person", "role", "is_active")
    list_filter = ("role", "is_active")
    search_fields = ("person__display_name",)
    raw_id_fields = ("person",)
    inlines = [PractitionerCommitmentInline]


@admin.register(ProjectCommitment)
class ProjectCommitmentAdmin(admin.ModelAdmin):
    list_display = ("practitioner", "project", "hours_per_day", "is_active", "start_date", "end_date")
    list_filter = ("is_active", "project")
    search_fields = ("practitioner__person__display_name", "project__name")
    raw_id_fields = ("practitioner", "project")


# ── Column ────────────────────────────────────────────────────────────────────

class TaskInlineForColumn(admin.TabularInline):
    model = Task
    extra = 1
    fields = ("title", "assignee", "due_date", "position", "sprint", "weight", "mission")
    ordering = ("position",)
    raw_id_fields = ("assignee", "mission", "sprint")


@admin.register(Column)
class ColumnAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "position", "can_add_task")
    list_filter = ("project",)
    ordering = ("position",)
    filter_horizontal = ("auditors",)
    inlines = [TaskInlineForColumn]


# ── Campaign ──────────────────────────────────────────────────────────────────

class MissionInline(admin.TabularInline):
    model = Mission
    extra = 1
    fields = ("title", "urgency", "impact", "owner")
    ordering = ("title",)


class CampaignAdminForm(forms.ModelForm):
    metadata = forms.JSONField(
        required=False,
        widget=PrettyJSONTextarea(attrs={"rows": 10, "class": "vLargeTextField"}),
    )

    class Meta:
        model = Campaign
        fields = "__all__"


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    form = CampaignAdminForm
    list_display = ("name", "project", "start_date", "end_date", "owner", "mission_count")
    list_filter = ("project", "start_date", "end_date")
    search_fields = ("name", "description")
    ordering = ("project", "start_date")
    inlines = [MissionInline]

    def mission_count(self, obj):
        return obj.missions.count()
    mission_count.short_description = "Missions"


# ── Mission ───────────────────────────────────────────────────────────────────

class DocumentationPageInline(admin.StackedInline):
    model = DocumentationPage
    extra = 0
    fields = ("title", "slug", "description", "is_manual")
    prepopulated_fields = {"slug": ("title",)}


class DocumentationSectionInline(SectionInlineMixin):
    model = DocumentationSection


class TaskInlineForMission(admin.TabularInline):
    model = Task
    extra = 1
    fields = ("title", "column", "assignee", "due_date", "position", "sprint", "weight")
    ordering = ("position",)
    raw_id_fields = ("assignee", "column", "sprint")


class MissionAdminForm(forms.ModelForm):
    metadata = forms.JSONField(
        required=False,
        widget=PrettyJSONTextarea(attrs={"rows": 10, "class": "vLargeTextField"}),
    )

    class Meta:
        model = Mission
        fields = "__all__"


@admin.register(Mission)
class MissionAdmin(admin.ModelAdmin):
    form = MissionAdminForm
    list_display = ("title", "campaign", "urgency", "impact", "owner", "task_count")
    list_filter = ("campaign", "urgency", "impact")
    search_fields = ("title", "description")
    ordering = ("campaign", "title")
    inlines = [TaskInlineForMission, DocumentationPageInline]

    def task_count(self, obj):
        return obj.tasks.count()
    task_count.short_description = "Tasks"


# ── Task ──────────────────────────────────────────────────────────────────────

class TaskAdminForm(forms.ModelForm):
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4, "class": "vLargeTextField"}),
    )
    metadata = forms.JSONField(
        required=False,
        widget=PrettyJSONTextarea(attrs={"rows": 10, "class": "vLargeTextField"}),
    )

    class Meta:
        model = Task
        fields = "__all__"


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    form = TaskAdminForm
    list_display = (
        "title", "column", "sprint", "mission",
        "assignee", "reviewer", "due_date", "position", "weight", "completed_at",
    )
    list_filter = ("due_date", "sprint", "mission")
    search_fields = ("title", "description")
    ordering = ("position",)
    raw_id_fields = ("assignee", "reviewer", "mission", "column", "sprint")
    actions = ["convert_to_event"]

    @admin.action(description="Convert selected tasks to events")
    def convert_to_event(self, request, queryset):
        def convert_one(task):
            venture = (
                getattr(task.mission.campaign.project, "venture", None)
                if task.mission
                else None
            )
            if not venture:
                raise ValueError(
                    f"Task '{task.title}' has no venture via its mission/campaign/project."
                )
            owner_person = task.assignee.person if task.assignee else None
            event = ScheduledEvent.objects.create(
                title=task.title,
                description=task.description or "",
                start_time=task.sprint.start_time if task.sprint else now(),
                end_time=task.due_date if task.due_date else now() + timedelta(days=1),
                owner=owner_person,
                category=None,
                public=False,
            )
            if owner_person:
                event.organizers.add(owner_person)
            return event

        result = BatchAction(queryset).run(convert_one)
        BatchAction.display_messages(result, self.message_user, request, verb="convert to event")


# ── Sprint ────────────────────────────────────────────────────────────────────

@admin.register(Sprint)
class SprintAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "start_time", "end_time")
    list_filter = ("project",)
    date_hierarchy = "start_time"
    actions = ["convert_to_event"]

    @admin.action(description="Convert selected sprints to events")
    def convert_to_event(self, request, queryset):
        def convert_one(sprint):
            venture = getattr(sprint.project, "venture", None)
            if not venture:
                raise ValueError(f"Sprint '{sprint.name}' has no venture via its project.")
            event = ScheduledEvent.objects.create(
                title=f"Sprint: {sprint.name}",
                description=f"Linked to project: {sprint.project.name}",
                start_time=sprint.start_time or now(),
                end_time=sprint.end_time or now() + timedelta(days=7),
                owner=sprint.project.project_lead,
                category=None,
                public=False,
            )
            if sprint.project.project_lead:
                event.organizers.add(sprint.project.project_lead)
            return event

        result = BatchAction(queryset).run(convert_one)
        BatchAction.display_messages(result, self.message_user, request, verb="convert to event")


# ── Documentation ─────────────────────────────────────────────────────────────

@admin.register(DocumentationPage)
class DocumentationPageAdmin(PageAdminMixin):
    list_display = ("title", "mission", "is_manual", "created_at")
    list_filter = ("is_manual",)
    search_fields = ["title", "description", "mission__title"]
    autocomplete_fields = ("mission",)
    readonly_fields = ["created_at"]
    inlines = [DocumentationSectionInline]
