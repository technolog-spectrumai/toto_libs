import uuid
from pathlib import PurePosixPath

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext as _


def message_attachment_upload_to(instance, filename):
    """Store attachments under ``<channel-slug>/<message-uuid><ext>``.

    The message id is already a UUID, so the path is collision-free without
    relying on the uploaded filename (kept separately in ``attachment_name``).
    """
    suffix = PurePosixPath(filename).suffix.lower()[:16]
    return f"{instance.channel.slug}/{instance.id}{suffix}"


def forum_attachment_storage():
    """Storage for message attachments — deliberately NOT ``MEDIA_ROOT``.

    nginx serves ``/media/`` unauthenticated with a 30-day cache, so anything under
    ``MEDIA_ROOT`` is world-readable to anyone who ever saw the URL, including a member
    who has since left. Attachments in a private channel must follow the same membership
    rule as the messages they belong to, so they live outside the web-served tree and are
    handed out only by ``api_views.MessageAttachmentApiView``, which checks ``can_read``.

    Override the location with ``settings.FORUM_ATTACHMENT_ROOT``.
    """
    return ForumAttachmentStorage()


def _attachment_root() -> str:
    from pathlib import Path

    return str(getattr(settings, "FORUM_ATTACHMENT_ROOT", "")
               or Path(settings.MEDIA_ROOT).parent / "forum_attachments")


class ForumAttachmentStorage(FileSystemStorage):
    """A FileSystemStorage that re-reads its root on every access.

    Django resolves a field's ``storage=`` callable EXACTLY ONCE, when the
    model class is built at import time, and ``FileSystemStorage`` caches
    ``location`` as a ``cached_property`` on top of that. The two together made
    ``settings.FORUM_ATTACHMENT_ROOT`` a setting that could only be read before
    any test could set it: ``@override_settings(FORUM_ATTACHMENT_ROOT=tmpdir)``
    changed nothing, and every suite that uploaded an attachment wrote into the
    running server's own tree instead. The evidence was sitting in it — channel
    slugs named ``hist``, ``images``, ``private`` and ``audio``, which are test
    fixtures, not rooms anybody made.

    Overriding both as plain properties (not ``cached_property``) is what makes
    the documented setting true. It matters more from here on: the Files tab
    reads this tree, and cleanup DELETES from it.
    """

    @property
    def base_location(self):
        return _attachment_root()

    @property
    def location(self):
        import os

        return os.path.abspath(self.base_location)


class ForumChannel(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    # Membership is ForumMember and only ForumMember. There used to be a parallel
    # ``participants`` M2M to AUTH_USER_MODEL; the two disagreed, and a user dropped from
    # one but not the other could still post over a raw websocket. See permissions.py.
    people = models.ManyToManyField(
        "people.Person",
        through="ForumMember",
        related_name="forum_channels",
        blank=True,
    )
    #: The room's vault library directory — one per channel, in the shared
    #: "forum" bucket, created lazily by library.ensure_channel_library().
    #: Null until the Files tab is first used. SET_NULL: deleting the vault
    #: side must not take the room with it.
    vault_directory = models.ForeignKey(
        "vault.VaultDirectory", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ForumMember(models.Model):
    channel = models.ForeignKey(
        ForumChannel,
        on_delete=models.CASCADE,
        related_name="forum_members",
    )
    person = models.ForeignKey(
        "people.Person",
        on_delete=models.CASCADE,
        related_name="forum_memberships",
        null=True,
        blank=True,
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["person__display_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "person"],
                condition=models.Q(person__isnull=False),
                name="unique_forum_channel_member",
            ),
            models.CheckConstraint(
                check=models.Q(person__isnull=False),
                name="forum_member_must_have_person",
            ),
        ]

    def __str__(self):
        return f"{self.display_name} in {self.channel.name}"

    def clean(self):
        super().clean()
        if not self.person_id:
            raise ValidationError(_("A member must have a person profile."))

    @property
    def display_name(self):
        if self.person:
            return self.person.full_name
        return "Unknown member"

    @property
    def avatar_url(self):
        if self.person and self.person.avatar:
            return self.person.avatar.url
        return "/static/img/avatars/default.png"

class ForumMessage(models.Model):
    """A persisted forum message, stored in plaintext.

    Confidentiality in transit is TLS; the row itself is readable. That is what makes
    permanent, searchable, paginated history possible — a member who joins today can read
    everything said before they arrived, and the server can run a text query over it.

    Messages are never expired automatically. Retention is deferred work.
    """

    MSG_TYPES = [
        ("chat_message", "chat_message"),
        ("image_message", "image_message"),
        ("voice_message", "voice_message"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(
        ForumChannel, on_delete=models.CASCADE, related_name="messages"
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    # Denormalized so history renders without re-resolving membership.
    sender_name = models.CharField(max_length=150, blank=True)
    sender_avatar_url = models.CharField(max_length=500, blank=True)
    msg_type = models.CharField(max_length=32, choices=MSG_TYPES, default="chat_message")

    # The message text. Blank for a bare image/voice post.
    body = models.TextField(blank=True)

    # Image/voice payloads live on disk, not in the row — a 10 MB upload used to become a
    # ~13.4 MB base64 blob replayed down the socket on every history load.
    attachment = models.FileField(
        upload_to=message_attachment_upload_to,
        storage=forum_attachment_storage,
        blank=True,
        null=True,
    )
    attachment_name = models.CharField(max_length=255, blank=True)
    attachment_mime = models.CharField(max_length=100, blank=True)
    attachment_size = models.PositiveIntegerField(null=True, blank=True)

    reply_to = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replies",
    )

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    # Soft delete: the row stays so replies keep their anchor and history keeps its shape.
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["channel", "created_at"]),
            models.Index(fields=["channel", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.msg_type} in {self.channel.slug} @ {self.created_at:%Y-%m-%d %H:%M}"

    @property
    def is_deleted(self):
        return self.deleted_at is not None




# ---------------------------------------------------------------------------
# Room polls
#
# A poll belongs to a room, and the ForeignKey below is what makes that true.
# It used to be a (scope_type, scope_id) string pair pointing at toto.polls,
# because that app could not import this one — a soft pointer is what you write
# when the engine must not depend on the place it is used. The engine is gone
# now: polls ARE a forum feature, so the room is a real relation, deleting a
# room takes its polls, and "all polls" is not a question anybody can ask by
# accident.
#
# What did NOT come across, and why: `kind` (its enum had one member), weight
# (a room is one member one vote — the old room electorate always answered 1),
# `metadata`, and the third visibility mode. What DID come across is the part
# that was hard-won: the clock semantics, the revision rules, and the refusal
# to alter a final ballot.
# ---------------------------------------------------------------------------


class PollStatus(models.TextChoices):
    OPEN = "open", _("Open")
    CLOSED = "closed", _("Closed")
    CANCELLED = "cancelled", _("Cancelled")


class Revisability(models.TextChoices):
    OPEN = "open", _("Answers may be changed")
    FINAL = "final", _("One answer, final")


class ResultVisibility(models.TextChoices):
    LIVE = "live", _("Everyone sees the count as it grows")
    ON_CLOSE = "on_close", _("The count appears when the poll closes")


class RoomPoll(models.Model):
    """One question asked inside one room."""

    channel = models.ForeignKey(ForumChannel, on_delete=models.CASCADE,
                                related_name="polls")
    title = models.CharField(max_length=150)
    question_text = models.CharField(max_length=300, blank=True)
    #: Unique per ROOM, not globally: two rooms both want a poll called
    #: "lunch", and scoping the slug is what lets them have one.
    slug = models.SlugField(max_length=170, blank=True)

    opens_at = models.DateTimeField(default=timezone.now)
    closes_at = models.DateTimeField(
        null=True, blank=True,
        help_text=_("Leave empty to stay open until somebody closes it."))
    status = models.CharField(max_length=10, choices=PollStatus.choices,
                              default=PollStatus.OPEN)
    closed_at = models.DateTimeField(null=True, blank=True)

    revisability = models.CharField(max_length=8, choices=Revisability.choices,
                                    default=Revisability.OPEN)
    visibility = models.CharField(max_length=20,
                                  choices=ResultVisibility.choices,
                                  default=ResultVisibility.LIVE)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                   blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["channel", "slug"],
                                    name="uniq_room_poll_slug_per_channel"),
        ]
        indexes = [
            models.Index(fields=["channel", "-created_at"]),
            models.Index(fields=["closes_at"]),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            # Through the same de-duplicating helper the service uses. A bare
            # slugify() fallback collides the moment two titles reduce to the
            # same thing — two Polish or emoji titles both fold to "poll" —
            # and the per-channel constraint turns that into a 500.
            from .voting import _unique_slug
            self.slug = _unique_slug(self.channel, self.title)
        super().save(*args, **kwargs)

    @property
    def is_open(self) -> bool:
        """Whether an answer would be accepted right now.

        Computed from the clock on every read, and deliberately not stored: a
        deadline enforced by a background job is a deadline that quietly does
        not apply when the worker is down. The consequence to know is that a
        poll past `closes_at` still reads `status == "open"` in the database —
        so ask THIS, never the column.
        """
        if self.status != PollStatus.OPEN:
            return False
        now = timezone.now()
        if self.opens_at and self.opens_at > now:
            return False
        # Strictly greater: at exactly closes_at the poll is shut.
        return self.closes_at is None or self.closes_at > now

    @property
    def has_closed(self) -> bool:
        return not self.is_open

    @property
    def results_visible(self) -> bool:
        return self.visibility == ResultVisibility.LIVE or self.has_closed

    def close(self, *, when=None):
        """Shut it by hand. Idempotent — closing a closed poll is not an error."""
        if self.status != PollStatus.OPEN:
            return self
        self.status = PollStatus.CLOSED
        self.closed_at = when or timezone.now()
        self.save(update_fields=["status", "closed_at"])
        return self


class PollChoice(models.Model):
    poll = models.ForeignKey(RoomPoll, on_delete=models.CASCADE,
                             related_name="choices")
    label = models.CharField(max_length=60)
    text = models.CharField(max_length=300, blank=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["poll", "label"],
                                    name="uniq_poll_choice_label"),
        ]

    def __str__(self):
        return self.label


class PollBallot(models.Model):
    """One member's answer. One row per voter per poll, enforced twice."""

    poll = models.ForeignKey(RoomPoll, on_delete=models.CASCADE,
                             related_name="ballots")
    choice = models.ForeignKey(PollChoice, on_delete=models.CASCADE,
                               related_name="ballots")
    voter = models.ForeignKey(settings.AUTH_USER_MODEL,
                              on_delete=models.CASCADE, related_name="+")
    cast_at = models.DateTimeField(auto_now_add=True)
    revised_at = models.DateTimeField(null=True, blank=True)
    revisions = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["cast_at"]
        constraints = [
            models.UniqueConstraint(fields=["poll", "voter"],
                                    name="uniq_poll_ballot_per_voter"),
        ]
        indexes = [models.Index(fields=["poll", "choice"])]

    def __str__(self):
        return f"{self.voter} → {self.choice}"

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
