from django.contrib import admin
from .models import Event, EventCategory
from toto.admin import BaseSerializableAdmin


@admin.register(EventCategory)
class EventCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)

@admin.register(Event)
class EventAdmin(BaseSerializableAdmin):
    list_display = ('title', 'organizer', 'category', 'start_time', 'end_time', 'public')
    list_filter = ('category', 'start_time')
    search_fields = ('title', 'description', 'location')
    autocomplete_fields = ('organizer', 'category')
