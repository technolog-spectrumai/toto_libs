from django.contrib import admin

from .models import TelegraphMember, TelegraphChannel


class TelegraphMemberInline(admin.TabularInline):
    model = TelegraphMember
    extra = 1
    fields = ("person", "is_active")
    autocomplete_fields = ("person",)


@admin.register(TelegraphChannel)
class TelegraphChannelAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "created_by", "member_count", "created_at")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name", "slug")
    filter_horizontal = ("participants",)
    inlines = [TelegraphMemberInline]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("telegraph_members")

    @admin.display(description="Members")
    def member_count(self, obj):
        return obj.telegraph_members.count()


@admin.register(TelegraphMember)
class TelegraphMemberAdmin(admin.ModelAdmin):
    list_display = ("display_name", "channel", "is_active", "joined_at")
    list_filter = ("is_active", "channel")
    search_fields = ("person__display_name", "person__email", "channel__name", "channel__slug")
    autocomplete_fields = ("person", "channel")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("channel", "person")
