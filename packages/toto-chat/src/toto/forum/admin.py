from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import (ForumCleanupRun, ForumMember, ForumChannel,
                     ForumMessage, ForumRetentionPolicy,
                     PollBallot, PollChoice, RoomPoll)


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

    @admin.display(description=_("Members"))
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

    A staff-set retention period removes older messages permanently: the
    platform's on the forum's Cleanup page (/forum/cleanup/), a room's own on
    that room's Settings tab; see `toto.forum.cleanup`.
    """

    list_display = ("channel", "sender_name", "msg_type", "created_at", "edited_at", "deleted_at")
    list_filter = ("msg_type", "channel", "created_at")
    search_fields = ("body", "sender_name", "channel__name", "channel__slug")
    readonly_fields = ("id", "created_at")
    date_hierarchy = "created_at"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("channel")


class PollChoiceInline(admin.TabularInline):
    model = PollChoice
    extra = 2
    fields = ("label", "text", "position")


@admin.register(RoomPoll)
class RoomPollAdmin(admin.ModelAdmin):
    list_display = ("title", "channel", "status", "closes_at", "answers",
                    "created_by", "created_at")
    list_filter = ("status", "revisability", "visibility")
    search_fields = ("title", "question_text", "channel__name")
    autocomplete_fields = ("channel",)
    inlines = [PollChoiceInline]
    readonly_fields = ("closed_at",)

    @admin.display(description=_("Answers"))
    def answers(self, obj):
        return obj.ballots.count()


@admin.register(PollBallot)
class PollBallotAdmin(admin.ModelAdmin):
    """Read-only on purpose.

    A ballot is somebody's answer, and an admin screen that could retype it
    would make every tally a claim about who last edited the database rather
    than about what people chose. Deleting one IS allowed: a poll must be
    removable, and `RoomPoll.delete()` cascades through here — but a FINAL
    poll's ballot refuses even that, on the model, which is the rule the
    service enforces for everyone else.
    """

    list_display = ("poll", "voter", "choice", "cast_at", "revisions")
    list_filter = ("poll__channel",)
    search_fields = ("poll__title", "voter__username")
    readonly_fields = ("poll", "choice", "voter", "cast_at", "revised_at",
                       "revisions")

    def has_add_permission(self, request):
        return False


@admin.register(ForumRetentionPolicy)
class ForumRetentionPolicyAdmin(admin.ModelAdmin):
    """The platform row plus one per room that set its own.

    Staff normally edit the platform row on the forum's Cleanup page and a
    room's row on that room's Settings tab.
    """

    list_display = ("retention_days", "enabled", "last_run_at",
                    "last_run_status")
    readonly_fields = ("last_run_at", "last_run_status", "last_error",
                       "updated_at", "updated_by")

    def has_add_permission(self, request):
        return not ForumRetentionPolicy.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ForumCleanupRun)
class ForumCleanupRunAdmin(admin.ModelAdmin):
    """The record of an irreversible act, and therefore read-only.

    Not deletable either — by anybody, including the person who started the
    run. The only evidence that history was destroyed must not be removable by
    whoever destroyed it.
    """

    list_display = ("started_at", "status", "triggered_by", "channel_name",
                    "boundary", "messages_deleted", "attachments_deleted",
                    "bytes_freed", "workflow_run_id")
    list_filter = ("status", "triggered_by")
    date_hierarchy = "started_at"
    readonly_fields = tuple(f.name for f in ForumCleanupRun._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
