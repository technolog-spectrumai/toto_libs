from django.contrib import admin

from .models import Participant, Room


class ParticipantInline(admin.TabularInline):
    model = Participant
    extra = 1
    fields = ("person", "is_active")
    autocomplete_fields = ("person",)


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "created_by", "participant_count", "created_at")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name", "slug")
    filter_horizontal = ("participants",)
    inlines = [ParticipantInline]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("chat_participants")

    @admin.display(description="Participants")
    def participant_count(self, obj):
        return obj.chat_participants.count()


@admin.register(Participant)
class ParticipantAdmin(admin.ModelAdmin):
    list_display = ("display_name", "room", "is_active", "joined_at")
    list_filter = ("is_active", "room")
    search_fields = ("person__display_name", "person__email", "room__name", "room__slug")
    autocomplete_fields = ("person", "room")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("room", "person")
