from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import (ForumMember, ForumChannel, ForumMessage,
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

    Retention is deferred work — there is no purge job.
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
