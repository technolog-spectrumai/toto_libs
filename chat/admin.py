from django.contrib import admin
from .models import Room, Message


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ("user", "content", "timestamp")
    can_delete = False


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("name", "created_by", "created_at")
    search_fields = ("name", "created_by__username")
    list_filter = ("created_at",)
    autocomplete_fields = ("created_by", "allowed_users")
    filter_horizontal = ("allowed_users",)
    inlines = [MessageInline]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("room", "user", "short_content", "timestamp")
    search_fields = ("content", "user__username", "room__name")
    list_filter = ("timestamp", "room")
    autocomplete_fields = ("user", "room")

    def short_content(self, obj):
        return obj.content[:50]
    short_content.short_description = "Content"
