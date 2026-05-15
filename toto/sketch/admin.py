from django.contrib import admin
from .models import Board, BoardObject


class BoardObjectInline(admin.TabularInline):
    model = BoardObject
    extra = 0
    fields = ("object_id", "object_type", "is_deleted", "updated_at")
    readonly_fields = ("object_id", "updated_at")
    ordering = ("-updated_at",)
    show_change_link = True


@admin.register(Board)
class BoardAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "owner", "created_at")
    search_fields = ("id", "name", "owner__username")
    list_filter = ("created_at",)
    ordering = ("-created_at",)

    inlines = [BoardObjectInline]


@admin.register(BoardObject)
class BoardObjectAdmin(admin.ModelAdmin):
    list_display = ("object_id", "object_type", "board", "is_deleted", "updated_at")
    search_fields = ("object_id", "object_type", "board__id")
    list_filter = ("object_type", "is_deleted", "created_at")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-updated_at",)

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return self.readonly_fields + ("object_id", "board")
        return self.readonly_fields
