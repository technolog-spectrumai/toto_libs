from django.contrib import admin
from .models import Project, Board, Column, Task, Sprint, Role, ColorMix
from django.utils.html import format_html
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
    list_display = ('name', 'board', 'position', 'color_mix')
    list_filter = ('board',)
    ordering = ('position',)
    inlines = [TaskInline]

@admin.register(ColorMix)
class ColorMixAdmin(admin.ModelAdmin):
    list_display = ('name', 'preview_light', 'preview_dark')
    readonly_fields = ('preview_light', 'preview_dark')
    fieldsets = (
        (None, {
            'fields': ('name',)
        }),
        ('Light Mode Colors', {
            'fields': ('bg_color_light', 'text_color_light', 'preview_light')
        }),
        ('Dark Mode Colors', {
            'fields': ('bg_color_dark', 'text_color_dark', 'preview_dark')
        }),
    )

    def preview_light(self, obj):
        return self._render_preview(obj.bg_color_light, obj.text_color_light)
    preview_light.short_description = "Light Preview"

    def preview_dark(self, obj):
        return self._render_preview(obj.bg_color_dark, obj.text_color_dark)
    preview_dark.short_description = "Dark Preview"

    def _render_preview(self, bg, text):
        return format_html(
            '<div style="background-color:{}; color:{}; padding:8px; border-radius:4px;">Sample Text</div>',
            bg, text
        )

class TaskAdminForm(forms.ModelForm):
    description = forms.CharField(widget=TipTapWidget())

    class Meta:
        model = Task
        fields = '__all__'

@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    form = TaskAdminForm
    list_display = ('title', 'column', 'assignee', 'due_date', 'position')
    list_filter = ('due_date', )
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
