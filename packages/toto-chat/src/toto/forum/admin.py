from django.contrib import admin

from .models import ForumMember, ForumChannel, ForumMessage


class ForumMemberInline(admin.TabularInline):
    model = ForumMember
    extra = 1
    fields = ("person", "is_active")
    autocomplete_fields = ("person",)


@admin.register(ForumChannel)
class ForumChannelAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "created_by", "member_count", "created_at")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name", "slug")
    inlines = [ForumMemberInline]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("forum_members")

    @admin.display(description="Members")
    def member_count(self, obj):
        return obj.forum_members.count()


@admin.register(ForumMember)
class ForumMemberAdmin(admin.ModelAdmin):
    list_display = ("display_name", "channel", "is_active", "joined_at")
    list_filter = ("is_active", "channel")
    search_fields = ("person__display_name", "person__email", "channel__name", "channel__slug")
    autocomplete_fields = ("person", "channel")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("channel", "person")


@admin.register(ForumMessage)
class ForumMessageAdmin(admin.ModelAdmin):
    """Messages are plaintext and permanent, so they are inspectable here.

    Retention is deferred work — there is no purge job.
    """

    list_display = ("channel", "sender_name", "msg_type", "created_at", "edited_at", "deleted_at")
    list_filter = ("msg_type", "channel", "created_at")
    search_fields = ("body", "sender_name", "channel__name", "channel__slug")
    readonly_fields = ("id", "created_at")
    date_hierarchy = "created_at"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("channel")
