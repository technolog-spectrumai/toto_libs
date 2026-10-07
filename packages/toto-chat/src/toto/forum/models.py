"""The forum's tables (2026-10-07, the simplified forum).

One channel per community, and the database says so: ``ForumChannel.community``
is a one-to-one. A channel has no name, no slug, no password and no member
list of its own: its address is the community's slug, its name the
community's name, and who may read it is who belongs to the community
(``access.py``).

Everything a member wrote is kept sealed under the channel's key
(``sealing.py``, ``keys.py``): a message's text, a poll's question, an
option's label and text, an image's bytes (a ``vault.VaultFile`` in the
channel's bucket). There is no plaintext column to fall back to. What is NOT
sealed is said in SECURITY.md: who sent a row and when, the sender's display
name on the row, an image's type and size, and the ballots.

``number`` and ``seq`` are the feed's two counters, both taken from
``ForumChannel.last_seq`` under a lock on the channel's row
(``channels.next_seq``): ``number`` is a row's place when it was made and
never changes (the order on the page, the cursor for older history); ``seq``
is the event that last changed it (posted, removed; opened, voted, closed,
removed), which is what a page asks "what changed after" by.
"""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
# Lazy: choices and help texts are read at import, in the platform's default
# language, so a plain gettext froze them in English for a Polish reader.
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy


class ForumChannel(models.Model):
    """The one channel of one community."""

    community = models.OneToOneField(
        "socialhub.Community", on_delete=models.CASCADE, related_name="forum_channel")
    #: The channel's bucket in the vault, where its images are kept sealed
    #: (``channels.ensure_bucket``). SET_NULL: deleting the vault's side does
    #: not take the channel; the next image makes a bucket again.
    bucket = models.ForeignKey(
        "vault.Bucket", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    #: The channel's event counter (module docstring).
    last_seq = models.PositiveBigIntegerField(default=0)
    #: Everything made before this instant was removed by a cleanup, rows and
    #: all. The feed always says it, so an open page drops what it holds.
    purged_before = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["community__name"]

    def __str__(self):
        return self.name

    @property
    def name(self) -> str:
        return self.community.name

    @property
    def slug(self) -> str:
        return self.community.slug


class ForumChannelKey(models.Model):
    """The key of one channel, wrapped — never in clear.

    The 32-byte channel key under the data key of the ``forum-channels``
    strongbox, which ``FORUM_VAULT_PASSWORD`` opens (``keys.py``). Without
    that secret the row is unreadable, and so is the channel.
    """

    channel = models.OneToOneField(ForumChannel, on_delete=models.CASCADE,
                                   related_name="channel_key")
    platform_wrapped = models.BinaryField()
    platform_nonce = models.BinaryField()
    platform_wrapped_key = models.ForeignKey("gervazy.WrappedDataKey",
                                             on_delete=models.PROTECT, related_name="+")
    version = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"channel key v{self.version} for {self.channel_id}"


class ForumMessage(models.Model):
    """One message: text, an image, or an image with text. Never changed
    once posted; removing it wipes its content and leaves a tombstone."""

    TEXT, IMAGE = "text", "image"
    KINDS = [(TEXT, "text"), (IMAGE, "image")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(ForumChannel, on_delete=models.CASCADE,
                                related_name="messages")
    number = models.PositiveBigIntegerField()
    seq = models.PositiveBigIntegerField(db_index=True)
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                               null=True, blank=True, related_name="+")
    #: The sender's display name when it was posted, so history reads without
    #: the account. An erased member's rows carry a neutral label instead.
    sender_name = models.CharField(max_length=150, blank=True)
    kind = models.CharField(max_length=8, choices=KINDS, default=TEXT)
    #: The text, as ``version || nonce || ciphertext+tag`` (sealing.py). None
    #: once the message is removed.
    body_sealed = models.BinaryField(null=True, blank=True, editable=False)
    #: The UTF-8 length of the text: what the price per KB is counted from.
    text_bytes = models.PositiveIntegerField(default=0)
    #: The image, sealed, in the channel's bucket. The forum owns exactly the
    #: vault files its rows point at.
    attachment = models.ForeignKey("vault.VaultFile", null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    #: The image's type, as its own bytes said it (never the sender's word).
    attachment_mime = models.CharField(max_length=32, blank=True)
    #: The image's size in bytes before sealing.
    attachment_size = models.PositiveIntegerField(null=True, blank=True)
    #: The operation id the page minted for this press, and the keyed digest
    #: of the request it was bound to (posting.py): a retry answers this row.
    op_key = models.CharField(max_length=36, blank=True, editable=False)
    op_digest = models.CharField(max_length=64, blank=True, editable=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["number"]
        constraints = [
            models.UniqueConstraint(fields=["channel", "number"],
                                    name="forum_message_number_per_channel"),
            models.UniqueConstraint(fields=["sender", "op_key"],
                                    condition=~models.Q(op_key=""),
                                    name="forum_message_sender_op"),
        ]
        indexes = [models.Index(fields=["channel", "seq"])]

    def __str__(self):
        return f"{self.kind} #{self.number} in channel {self.channel_id}"

    @property
    def is_removed(self) -> bool:
        return self.removed_at is not None


# ---------------------------------------------------------------------------
# Polls. The rules are voting.py's and they are the old forum's: one member,
# one answer; the order of the checks in ``cast``; a revision never re-reads
# the answerer's standing; a final answer is never altered.
# ---------------------------------------------------------------------------


class PollStatus(models.TextChoices):
    # A poll's state, said of the poll (Polish: "Otwarta", not "Otwórz").
    OPEN = "open", pgettext_lazy("poll status", "Open")
    CLOSED = "closed", pgettext_lazy("poll status", "Closed")


class Revisability(models.TextChoices):
    OPEN = "open", _("Answers may be changed")
    FINAL = "final", _("One answer, final")


class ResultVisibility(models.TextChoices):
    LIVE = "live", _("Everyone sees the count as it grows")
    ON_CLOSE = "on_close", _("The count appears when the poll closes")


class ChannelPoll(models.Model):
    """One question asked in one channel."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(ForumChannel, on_delete=models.CASCADE,
                                related_name="polls")
    number = models.PositiveBigIntegerField()
    seq = models.PositiveBigIntegerField(db_index=True)
    #: The question, sealed. None once the poll is removed.
    title_sealed = models.BinaryField(null=True, blank=True, editable=False)
    closes_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=PollStatus.choices,
                              default=PollStatus.OPEN)
    closed_at = models.DateTimeField(null=True, blank=True)
    revisability = models.CharField(max_length=8, choices=Revisability.choices,
                                    default=Revisability.OPEN)
    visibility = models.CharField(max_length=20, choices=ResultVisibility.choices,
                                  default=ResultVisibility.LIVE)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    #: Who opened it, by display name, as a message keeps its sender's.
    opener_name = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["number"]
        constraints = [
            models.UniqueConstraint(fields=["channel", "number"],
                                    name="forum_poll_number_per_channel"),
        ]
        indexes = [models.Index(fields=["channel", "seq"])]

    def __str__(self):
        return f"poll #{self.number} in channel {self.channel_id}"

    @property
    def is_removed(self) -> bool:
        return self.removed_at is not None

    @property
    def is_open(self) -> bool:
        """Whether an answer would be accepted right now.

        Computed from the clock on every read, and deliberately not stored: a
        deadline enforced by a background job is a deadline that quietly does
        not apply when the worker is down. So a poll past ``closes_at`` still
        reads ``status == "open"`` in the database: ask THIS, never the column.
        """
        if self.removed_at is not None or self.status != PollStatus.OPEN:
            return False
        # Strictly greater: at exactly closes_at the poll is shut.
        return self.closes_at is None or self.closes_at > timezone.now()

    @property
    def results_visible(self) -> bool:
        return self.visibility == ResultVisibility.LIVE or not self.is_open

    def close(self, *, when=None):
        """Shut it by hand. Idempotent: closing a closed poll is not an error."""
        if self.status != PollStatus.OPEN:
            return self
        self.status = PollStatus.CLOSED
        self.closed_at = when or timezone.now()
        self.save(update_fields=["status", "closed_at"])
        return self


class PollChoice(models.Model):
    poll = models.ForeignKey(ChannelPoll, on_delete=models.CASCADE, related_name="choices")
    position = models.PositiveSmallIntegerField(default=0)
    #: The option, sealed: JSON ``{"label", "text"}`` (voting.py).
    sealed = models.BinaryField(editable=False)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["poll", "position"],
                                    name="forum_poll_choice_position"),
        ]

    def __str__(self):
        return f"option {self.position} of poll {self.poll_id}"


class PollBallot(models.Model):
    """One member's answer. One row per voter per poll, enforced twice."""

    poll = models.ForeignKey(ChannelPoll, on_delete=models.CASCADE, related_name="ballots")
    choice = models.ForeignKey(PollChoice, on_delete=models.CASCADE, related_name="ballots")
    voter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name="+")
    cast_at = models.DateTimeField(auto_now_add=True)
    revised_at = models.DateTimeField(null=True, blank=True)
    revisions = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["cast_at"]
        constraints = [
            models.UniqueConstraint(fields=["poll", "voter"],
                                    name="forum_poll_ballot_per_voter"),
        ]
        indexes = [models.Index(fields=["poll", "choice"])]

    def __str__(self):
        return f"ballot of {self.voter_id} in poll {self.poll_id}"

    def save(self, *args, **kwargs):
        # A final poll's ballot is written once and never edited. The rule
        # lives on the model rather than only in the service so that a shell,
        # an admin save or a future caller cannot walk around it.
        if self.pk is not None and self.poll.revisability == Revisability.FINAL:
            raise ValidationError(_("A final answer is never altered."))
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.poll.revisability == Revisability.FINAL:
            raise ValidationError(_("A final answer is never withdrawn."))
        return super().delete(*args, **kwargs)


# ---------------------------------------------------------------------------
# Settings and cleanup records. The page that edits the first and the sweep
# that writes the second are the next stage's; the tables are made with the
# rest so a database is built once.
# ---------------------------------------------------------------------------


class ForumSettings(models.Model):
    """The forum's dials, one row for the platform (``current()``).

    In the database and not in settings.py because administrators choose
    them on the forum's Settings page: a setting would need a redeploy.
    """

    #: Off on arrival: an app that begins destroying history the moment it
    #: is installed is a bug with a release note.
    retention_enabled = models.BooleanField(default=False)
    retention_days = models.PositiveIntegerField(
        default=365, validators=[MinValueValidator(1), MaxValueValidator(3650)],
        help_text=_("Messages and polls older than this are permanently removed."))
    #: How often an open channel page asks for what changed, in seconds.
    refresh_seconds = models.PositiveSmallIntegerField(
        default=5, validators=[MinValueValidator(2), MaxValueValidator(120)],
        help_text=_("How often an open channel asks for new messages, in seconds."))
    #: A post whose text and picture together are smaller than this many
    #: kilobytes costs nothing (the owner, 2026-10-07: "make forum messages
    #: free below threshold (like 300kB) - make this setting param"). 0: no
    #: post is free. Read by ``billing.is_free``.
    free_below_kb = models.PositiveIntegerField(
        default=300, validators=[MaxValueValidator(102400)],
        help_text=_("A message whose text and picture together are smaller than this costs nothing. 0 charges every message."))
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name = _("forum settings")
        verbose_name_plural = _("forum settings")

    def __str__(self):
        state = _("on") if self.retention_enabled else _("off")
        return f"retention {self.retention_days} days ({state}), refresh {self.refresh_seconds} s"

    @classmethod
    def current(cls):
        """The one row, made on first use."""
        row, _created = cls.objects.get_or_create(pk=1)
        return row

    def boundary(self, now=None):
        """The cutoff: everything made strictly before this goes."""
        from datetime import timedelta

        return (now or timezone.now()) - timedelta(days=self.retention_days)


class RunStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    RUNNING = "running", _("Running")
    SUCCESS = "success", _("Finished")
    PARTIAL = "partial", _("Stopped part-way")
    FAILED = "failed", _("Failed")


class TriggeredBy(models.TextChoices):
    BEAT = "beat", _("On schedule")
    MANUAL = "manual", _("Started by a person")


class ForumCleanupRun(models.Model):
    """One sweep, and what it destroyed: the only record that an irreversible
    thing happened.

    NOTE for any aggregate over this model: ``Meta.ordering`` folds into a
    GROUP BY, so every ``.values().annotate()`` needs a trailing ``.order_by()``.
    """

    #: The channel this sweep covered, or NULL for every channel. SET_NULL:
    #: a community that is deleted must not erase the record.
    channel = models.ForeignKey(ForumChannel, on_delete=models.SET_NULL, null=True,
                                blank=True, related_name="cleanup_runs")
    channel_name = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=RunStatus.choices,
                              default=RunStatus.PENDING, db_index=True)
    triggered_by = models.CharField(max_length=8, choices=TriggeredBy.choices,
                                    default=TriggeredBy.BEAT)
    triggered_by_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                          on_delete=models.SET_NULL, related_name="+")
    #: The cutoff actually used and the age it came from, copied in so a
    #: later change of the dial does not rewrite what this run says it did.
    boundary = models.DateTimeField()
    retention_days = models.PositiveIntegerField()
    messages_deleted = models.PositiveIntegerField(default=0)
    attachments_deleted = models.PositiveIntegerField(default=0)
    bytes_freed = models.PositiveBigIntegerField(default=0)
    blobs_missing = models.PositiveIntegerField(default=0)
    polls_deleted = models.PositiveIntegerField(default=0)
    ballots_deleted = models.PositiveIntegerField(default=0)
    channels_touched = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    #: The workflow run that carries this row: a plain id, so this app
    #: migrates without toto.workflows.
    workflow_run_id = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    task_id = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["status", "started_at"])]

    def __str__(self):
        return f"{self.get_status_display()} — {self.messages_deleted} removed"

    @property
    def is_finished(self) -> bool:
        return self.status in (RunStatus.SUCCESS, RunStatus.PARTIAL, RunStatus.FAILED)


from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent  # noqa: E402


class ForumUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        verbose_name = "Forum usage event"
        verbose_name_plural = "Forum usage events"


class ForumQuotaPolicy(AbstractQuotaPolicy):
    events = ForumUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = "Forum quota policy"
        verbose_name_plural = "Forum quota policies"
