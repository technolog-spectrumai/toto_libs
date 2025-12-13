from django.contrib import admin
from django import forms
from django_json_widget.widgets import JSONEditorWidget
from .models import Project, Column, Task, Sprint, Mission, Campaign
from .batch import BatchAction
from events.models import Event
from django.utils.timezone import now
from datetime import timedelta


# 🧠 Project Admin with Inline Campaigns
class CampaignInline(admin.TabularInline):
    model = Campaign
    extra = 1
    fields = ('name', 'start_date', 'end_date', 'owner')
    ordering = ('start_date',)

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner')
    search_fields = ('name', 'description')
    filter_horizontal = ('collaborators',)
    inlines = [CampaignInline]


# 🧱 Column Admin with Inline Tasks
class TaskInlineForColumn(admin.TabularInline):
    model = Task
    extra = 1
    fields = ('title', 'assignee', 'due_date', 'position', 'sprint', 'weight', 'mission')
    ordering = ('position',)

@admin.register(Column)
class ColumnAdmin(admin.ModelAdmin):
    list_display = ('name', 'project', 'position')
    list_filter = ('project',)
    ordering = ('position',)
    inlines = [TaskInlineForColumn]


# 📣 Campaign Admin with Inline Missions
class MissionInline(admin.TabularInline):
    model = Mission
    extra = 1
    fields = ('title', 'urgency', 'impact', 'owner')
    ordering = ('title',)

class CampaignAdminForm(forms.ModelForm):
    metadata = forms.JSONField(widget=JSONEditorWidget, required=False)

    class Meta:
        model = Campaign
        fields = '__all__'

@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    form = CampaignAdminForm
    list_display = ('name', 'project', 'start_date', 'end_date', 'owner', 'mission_count')
    list_filter = ('project', 'start_date', 'end_date')
    search_fields = ('name', 'description')
    ordering = ('project', 'start_date')
    inlines = [MissionInline]

    def mission_count(self, obj):
        return obj.missions.count()
    mission_count.short_description = "Missions"


# 🎯 Mission Admin with Inline Tasks
class TaskInlineForMission(admin.TabularInline):
    model = Task
    extra = 1
    fields = ('title', 'column', 'assignee', 'due_date', 'position', 'sprint', 'weight')
    ordering = ('position',)

class MissionAdminForm(forms.ModelForm):
    metadata = forms.JSONField(widget=JSONEditorWidget, required=False)

    class Meta:
        model = Mission
        fields = '__all__'

@admin.register(Mission)
class MissionAdmin(admin.ModelAdmin):
    form = MissionAdminForm
    list_display = ('title', 'campaign', 'urgency', 'impact', 'owner', 'task_count')
    list_filter = ('campaign', 'urgency', 'impact')
    search_fields = ('title', 'description')
    ordering = ('campaign', 'title')
    inlines = [TaskInlineForMission]

    def task_count(self, obj):
        return obj.tasks.count()
    task_count.short_description = "Tasks"


# 📝 Task Admin with JSON editor and Event Conversion
class TaskAdminForm(forms.ModelForm):
    description = forms.CharField()
    metadata = forms.JSONField(widget=JSONEditorWidget, required=False)

    class Meta:
        model = Task
        fields = '__all__'

@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    form = TaskAdminForm
    list_display = (
        'title', 'column', 'sprint', 'mission', 'assignee',
        'due_date', 'position', 'weight'
    )
    list_filter = ('due_date', 'sprint', 'mission')
    search_fields = ('title', 'description')
    ordering = ('position',)
    actions = ['convert_to_event']

    @admin.action(description="Convert selected tasks to events")
    def convert_to_event(self, request, queryset):
        def convert_one(task):
            venture = getattr(task.mission.campaign.project, 'venture', None) if task.mission else None
            if not venture:
                raise ValueError(f"Task '{task.title}' has no venture via its mission/campaign/project.")
            return Event.objects.create(
                title=task.title,
                description=task.description,
                location=f"Column: {task.column.name}",
                start_time=task.sprint.start_time if task.sprint else now(),
                end_time=task.due_date if task.due_date else now() + timedelta(days=1),
                venture=venture,
                organizer=task.assignee,
                category=None,
                public=False
            )
        result = BatchAction(queryset).run(convert_one)
        BatchAction.display_messages(result, self.message_user, request, verb="convert to event")


# 🚀 Sprint Admin with Event Conversion
@admin.register(Sprint)
class SprintAdmin(admin.ModelAdmin):
    list_display = ('name', 'project', 'start_time', 'end_time')
    list_filter = ('project',)
    date_hierarchy = 'start_time'
    actions = ['convert_to_event']

    @admin.action(description="Convert selected sprints to events")
    def convert_to_event(self, request, queryset):
        def convert_one(sprint):
            venture = getattr(sprint.project, 'venture', None)
            if not venture:
                raise ValueError(f"Sprint '{sprint.name}' has no venture via its project.")
            return Event.objects.create(
                title=f"Sprint: {sprint.name}",
                description=f"Linked to project: {sprint.project.name}",
                location="Kanban Board",
                start_time=sprint.start_time or now(),
                end_time=sprint.end_time or now() + timedelta(days=7),
                venture=venture,
                organizer=sprint.project.owner,
                category=None,
                public=False
            )
        result = BatchAction(queryset).run(convert_one)
        BatchAction.display_messages(result, self.message_user, request, verb="convert to event")
