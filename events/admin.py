from django.contrib import admin
from .models import Event, EventCategory, EventRegistration


@admin.register(EventCategory)
class EventCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)

@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'venture', 'organizer', 'category', 'start_time', 'end_time')
    list_filter = ('venture', 'category', 'start_time')
    search_fields = ('title', 'description', 'location')
    autocomplete_fields = ('venture', 'organizer', 'category')

@admin.register(EventRegistration)
class EventRegistrationAdmin(admin.ModelAdmin):
    list_display = ('event', 'user', 'registered_at')
    list_filter = ('event', 'registered_at')
    search_fields = ('user__username', 'event__title')
    autocomplete_fields = ('event', 'user')
