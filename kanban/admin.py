from django.contrib import admin
from .models import Project, Board, Column, Task, Sprint
from django_tiptap.widgets import TipTapWidget
from django import forms


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner')
    search_fields = ('name', 'description')
    filter_horizontal = ('collaborators',)


@admin.register(Board)
class BoardAdmin(admin.ModelAdmin):
    list_display = ('name', 'project')
    search_fields = ('name',)


class TaskInline(admin.TabularInline):
    model = Task
    extra = 1
    fields = ('title', 'assignee', 'due_date', 'position')
    ordering = ('position',)


@admin.register(Column)
class ColumnAdmin(admin.ModelAdmin):
    list_display = ('name', 'board', 'position')
    list_filter = ('board',)
    ordering = ('position',)
    inlines = [TaskInline]


class TaskAdminForm(forms.ModelForm):
    description = forms.CharField(widget=TipTapWidget())

    class Meta:
        model = Task
        fields = '__all__'


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    form = TaskAdminForm
    list_display = ('title', 'column', 'assignee', 'due_date', 'position')
    list_filter = ('due_date',)
    search_fields = ('title', 'description')
    ordering = ('position',)


@admin.register(Sprint)
class SprintAdmin(admin.ModelAdmin):
    list_display = ('name', 'project', 'start', 'end')
    filter_horizontal = ('tasks',)
    date_hierarchy = 'start'
