from django.contrib import admin
from django import forms
from django.utils import timezone
from datetime import timedelta
from django_tiptap.widgets import TipTapWidget
from .models import Project, Column, Task, Sprint
from .batch import BatchAction


# 🧠 Project Admin
@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner')
    search_fields = ('name', 'description')
    filter_horizontal = ('collaborators',)


# 🧱 Column Admin with Inline Tasks
class TaskInline(admin.TabularInline):
    model = Task
    extra = 1
    fields = ('title', 'assignee', 'due_date', 'position', 'sprint')
    ordering = ('position',)


@admin.register(Column)
class ColumnAdmin(admin.ModelAdmin):
    list_display = ('name', 'project', 'position')
    list_filter = ('project',)
    ordering = ('position',)
    inlines = [TaskInline]


# 📝 Task Admin with TipTap and Event Conversion
class TaskAdminForm(forms.ModelForm):
    description = forms.CharField(widget=TipTapWidget())

    class Meta:
        model = Task
        fields = '__all__'


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    form = TaskAdminForm
    list_display = ('title', 'column', 'sprint', 'assignee', 'due_date', 'position')
    list_filter = ('due_date', 'sprint')
    search_fields = ('title', 'description')
    ordering = ('position',)
    actions = ['convert_to_event']

    @admin.action(description="Convert selected tasks to events")
    def convert_to_event(self, request, queryset):
        from events.models import Event
        def convert_one(task):
            return Event.objects.create(
                title=task.title,
                description=task.description,
                location=f"Column: {task.column.name}",
                start_time=task.sprint.start_time if task.sprint else timezone.now(),
                end_time=task.due_date if task.due_date else timezone.now() + timedelta(days=1),
                venture=None,
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
        from events.models import Event
        def convert_one(sprint):
            return Event.objects.create(
                title=f"Sprint: {sprint.name}",
                description=f"Linked to project: {sprint.project.name}",
                location="Kanban Board",
                start_time=sprint.start_time,
                end_time=sprint.end_time,
                venture=None,
                organizer=sprint.project.owner,
                category=None,
                public=False
            )
        result = BatchAction(queryset).run(convert_one)
        BatchAction.display_messages(result, self.message_user, request, verb="convert to event")
