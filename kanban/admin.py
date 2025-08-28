from django.contrib import admin
from .models import Project, Board, Column, Task, Sprint, Role

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner')
    search_fields = ('name', 'description')
    filter_horizontal = ('collaborators',)

@admin.register(Board)
class BoardAdmin(admin.ModelAdmin):
    list_display = ('name', 'project')
    search_fields = ('name',)

@admin.register(Column)
class ColumnAdmin(admin.ModelAdmin):
    list_display = ('name', 'board', 'position')
    list_filter = ('board',)
    ordering = ('position',)

@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'column', 'assignee', 'due_date', 'completed', 'position', 'story_points')
    list_filter = ('completed', 'due_date')
    search_fields = ('title', 'description')
    ordering = ('position',)

@admin.register(Sprint)
class SprintAdmin(admin.ModelAdmin):
    list_display = ('name', 'project', 'start', 'end')
    filter_horizontal = ('tasks',)
    date_hierarchy = 'start'

@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'project')
    filter_horizontal = ('users',)
