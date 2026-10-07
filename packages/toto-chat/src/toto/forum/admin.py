"""The forum in the Django admin: what there is, to look at.

Nothing a member wrote is readable here: text, questions and options are
sealed and their columns are not shown. Channels are made by
``channels.ensure_channel`` and never by hand; ballots, cleanup records and
the settings are read-only (the settings have their own page).
"""

from django.contrib import admin

from .models import (ChannelPoll, ForumChannel, ForumCleanupRun, ForumMessage,
                     ForumSettings, PollBallot)


class _ReadOnly(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ForumChannel)
class ForumChannelAdmin(_ReadOnly):
    list_display = ("community", "bucket", "last_seq", "purged_before", "created_at")
    search_fields = ("community__name", "community__slug")


@admin.register(ForumMessage)
class ForumMessageAdmin(_ReadOnly):
    list_display = ("channel", "number", "kind", "sender_name", "created_at", "removed_at")
    list_filter = ("kind",)
    search_fields = ("sender_name", "channel__community__name")
    exclude = ("body_sealed",)
    date_hierarchy = "created_at"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("channel__community")


@admin.register(ChannelPoll)
class ChannelPollAdmin(_ReadOnly):
    list_display = ("channel", "number", "status", "closes_at", "opener_name",
                    "created_at", "removed_at")
    list_filter = ("status", "revisability", "visibility")
    exclude = ("title_sealed",)


@admin.register(PollBallot)
class PollBallotAdmin(_ReadOnly):
    """Read-only on purpose: a ballot is somebody's answer, and a screen
    that could retype it would make every tally a claim about who last
    edited the database."""

    list_display = ("poll", "voter", "choice", "cast_at", "revisions")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ForumSettings)
class ForumSettingsAdmin(_ReadOnly):
    """To look at. The dials are changed on the forum's Settings page
    (``/forum/settings/``), which asks for an administrator of the platform;
    a staff account with a model permission must not be a second way in."""

    list_display = ("retention_enabled", "retention_days", "refresh_seconds", "updated_at",
                    "updated_by")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ForumCleanupRun)
class ForumCleanupRunAdmin(_ReadOnly):
    """The record of an irreversible act: read-only, and not deletable by
    anybody, the person who started the run included."""

    list_display = ("started_at", "status", "triggered_by", "channel_name", "boundary",
                    "messages_deleted", "attachments_deleted", "bytes_freed")
    list_filter = ("status", "triggered_by")
    date_hierarchy = "started_at"

    def has_delete_permission(self, request, obj=None):
        return False
