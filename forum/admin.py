# forum/admin.py
from django.contrib import admin
from .models import Room, Message

class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ('timestamp',)
    fields = ('content', 'parent', 'timestamp')
    show_change_link = True

class RoomAdmin(admin.ModelAdmin):
    list_display = ('name', 'max_bytes')
    inlines = [MessageInline]

class MessageAdmin(admin.ModelAdmin):
    list_display = ('short_content', 'room', 'parent', 'timestamp')
    list_filter = ('room',)
    search_fields = ('content',)

    def short_content(self, obj):
        return obj.content[:50] + ('...' if len(obj.content) > 50 else '')
    short_content.short_description = 'Content'

admin.site.register(Room, RoomAdmin)
admin.site.register(Message, MessageAdmin)
