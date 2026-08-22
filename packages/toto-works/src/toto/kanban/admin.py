from datetime import timedelta
import json

from django import forms
from django.contrib import admin
from django.utils.timezone import now

from .models import (
    Project, Task, TaskRelation, Sprint, Mission, MissionAttachment, Campaign,
    DocumentationPage,
    Practitioner, ProjectCommitment,
    Assignment, ConsensusPolicy, Review, Submission, SubmissionFile,
    TaskReviewer,
)
from toto.core.batch import BatchAction
from toto.events.models import ScheduledEvent
from toto.verbena.admin import PageAdminMixin


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
    filter_horizontal = ("auditors",)
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

class TaskInlineForMission(admin.TabularInline):
    model = Task
    extra = 1
    fields = ("title", "status", "assignee", "due_date", "position", "sprint", "weight")
    ordering = ("position",)
    raw_id_fields = ("assignee", "sprint")


class MissionAdminForm(forms.ModelForm):
    metadata = forms.JSONField(
        required=False,
        widget=PrettyJSONTextarea(attrs={"rows": 10, "class": "vLargeTextField"}),
    )

    class Meta:
        model = Mission
        fields = "__all__"


class MissionAttachmentInline(admin.TabularInline):
    model = MissionAttachment
    extra = 0
    fields = ("vault_file", "label", "added_by", "created_at")
    readonly_fields = ("created_at",)
    raw_id_fields = ("vault_file", "added_by")


@admin.register(Mission)
class MissionAdmin(admin.ModelAdmin):
    form = MissionAdminForm
    list_display = ("title", "campaign", "visibility", "urgency", "impact", "owner",
                    "task_count")
    list_filter = ("visibility", "campaign", "urgency", "impact")
    search_fields = ("title", "description")
    ordering = ("campaign", "title")
    filter_horizontal = ("visible_to",)
    raw_id_fields = ("owner", "location", "route", "zone", "calendar_event")
    inlines = [TaskInlineForMission, MissionAttachmentInline]

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
        "title", "status", "sprint", "mission",
        "assignee", "reviewer", "due_date", "position", "weight", "completed_at",
    )
    list_filter = ("status", "due_date", "sprint", "mission")
    search_fields = ("title", "description")
    ordering = ("position",)
    raw_id_fields = ("assignee", "reviewer", "mission", "sprint", "location", "calendar_event")
    # Derived from status by Task.save; editable here it would silently revert.
    readonly_fields = ("completed_at",)
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
    """The page's identity and its place in the tree. Never its prose.

    There is no section inline any more — the body is one HTML field written by
    the cyprian bridge — and `body_html` is read-only here on purpose: the writer
    owns it, and a textarea that silently loses the editor's markup would be a
    trap rather than a convenience.

    `vault_file` is not an editable field and must never become one. It is the
    column the cyprian bridge authorises against, so anyone who could set it here
    could hand themselves any document on the instance. It is `editable=False` on
    the model, which already keeps it out of this form; it is named in
    readonly_fields so that it is visible, and so that the reason is written next
    to it.
    """

    list_display = ("title", "project", "parent", "mission", "is_manual", "created_at")
    list_filter = ("is_manual", "project")
    search_fields = ["title", "description", "mission__title", "project__name"]
    autocomplete_fields = ("mission",)
    raw_id_fields = ("project", "parent")
    readonly_fields = ["created_at", "body_html", "vault_file"]


# ── Task relations ────────────────────────────────────────────────────────────

@admin.register(TaskRelation)
class TaskRelationAdmin(admin.ModelAdmin):
    list_display = ("from_task", "relation_type", "to_task", "created_by", "created_at")
    list_filter = ("relation_type",)
    search_fields = ("from_task__title", "to_task__title", "note")
    raw_id_fields = ("from_task", "to_task", "created_by")


# ── The work engine ───────────────────────────────────────────────────────────
#
# ConsensusPolicy is the one that MUST be here. The design claim is that the
# review rule is editable data rather than a branch in code — and a rule nobody
# can edit is a branch in code with extra steps.


@admin.register(ConsensusPolicy)
class ConsensusPolicyAdmin(admin.ModelAdmin):
    list_display = (
        "name", "required_reviews", "required_accepts", "reject_threshold",
        "changes_threshold", "is_default",
    )
    list_filter = ("is_default",)
    search_fields = ("name",)


class SubmissionFileInline(admin.TabularInline):
    model = SubmissionFile
    extra = 0
    raw_id_fields = ("vault_file",)


class ReviewInline(admin.TabularInline):
    model = Review
    extra = 0
    raw_id_fields = ("reviewer",)
    readonly_fields = ("created_at",)


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    """Read-mostly on purpose.

    A submitted submission is frozen — ``Submission.save`` refuses a content
    edit and a CheckConstraint backs it — so offering those fields here would
    render a form that throws on save. The state fields stay editable because
    an operator occasionally has to withdraw something; the CONTENT never does.
    """

    list_display = (
        "pk", "task", "submitted_by", "state", "resolution", "submitted_at",
        "resolved_at",
    )
    list_filter = ("state", "resolution")
    search_fields = ("task__title", "submitted_by__display_name", "notes")
    raw_id_fields = ("task", "submitted_by", "supersedes")
    inlines = [SubmissionFileInline, ReviewInline]

    def get_readonly_fields(self, request, obj=None):
        base = ["created_at", "submitted_at", "resolved_at"]
        if obj is not None and obj.is_submitted:
            base += ["task", "submitted_by", "notes", "metadata", "supersedes"]
        return base


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("submission", "reviewer", "verdict", "created_at")
    list_filter = ("verdict",)
    search_fields = ("reviewer__display_name", "comment")
    raw_id_fields = ("submission", "reviewer")


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ("task", "person", "claimed_at", "released_at")
    list_filter = ("released_at",)
    search_fields = ("task__title", "person__display_name")
    raw_id_fields = ("task", "person")


@admin.register(TaskReviewer)
class TaskReviewerAdmin(admin.ModelAdmin):
    list_display = ("task", "person", "created_at")
    search_fields = ("task__title", "person__display_name")
    raw_id_fields = ("task", "person")
