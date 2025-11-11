from django.contrib import admin
from .models import Event, EventCategory


@admin.register(EventCategory)
class EventCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)

@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'company', 'organizer', 'category', 'start_time', 'end_time', 'public')
    list_filter = ('company', 'category', 'start_time')
    search_fields = ('title', 'description', 'location')
    autocomplete_fields = ('company', 'organizer', 'category')
