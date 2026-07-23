import uuid
from pathlib import PurePosixPath

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


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
    from pathlib import Path

    from django.core.files.storage import FileSystemStorage

    root = getattr(settings, "FORUM_ATTACHMENT_ROOT", "") or (
        Path(settings.MEDIA_ROOT).parent / "forum_attachments"
    )
    return FileSystemStorage(location=str(root))


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
            raise ValidationError("Members must be human users (person required).")

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

    @property
    def participant_type(self):
        return "human"


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


