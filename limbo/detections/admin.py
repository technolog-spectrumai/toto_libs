from django.contrib import admin
from .models import (
    DetectionCategory, Detection, DetectionHandle,
)


@admin.register(DetectionCategory)
class DetectionCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'parent', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name',)
    prepopulated_fields = {'slug': ('name',)}


class DetectionHandleInline(admin.TabularInline):
    model = DetectionHandle
    extra = 0
    fields = ('assigned_to', 'status', 'note')


@admin.register(Detection)
class DetectionAdmin(admin.ModelAdmin):
    list_display = ('title', 'detection_type', 'severity', 'status', 'reported_by', 'mitigation_task', 'start_time', 'location_label')
    list_filter = ('detection_type', 'severity', 'status', 'category')
    search_fields = ('title', 'description')
    filter_horizontal = ('involved_persons',)
    inlines = [DetectionHandleInline]
    readonly_fields = ('created_at', 'updated_at')

    def location_label(self, obj):
        return obj.location_label
    location_label.short_description = 'Location'


@admin.register(DetectionHandle)
class DetectionHandleAdmin(admin.ModelAdmin):
    list_display = ('detection', 'assigned_to', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('detection__title',)
