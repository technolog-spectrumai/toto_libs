import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class TelegraphChannel(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    participants = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="telegraph_channels",
        blank=True,
    )
    people = models.ManyToManyField(
        "people.Person",
        through="TelegraphMember",
        related_name="telegraph_channels",
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # ── Discord-style persistent history (encrypted at rest) ──────────────────
    # How long a regular message lives before it is purged. Default 24h.
    message_ttl_seconds = models.PositiveIntegerField(default=86400)
    # This channel's gervazy data key (one DEK per channel), created lazily on the
    # first stored message. Only the FK lives here; key material stays inside the
    # gervazy envelope (VMK → UKEK → TELEGRAPH_VAULT_PASSWORD), never on this row.
    dek = models.ForeignKey(
        "gervazy.WrappedDataKey",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="telegraph_channels",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def message_ttl(self):
        from datetime import timedelta

        return timedelta(seconds=self.message_ttl_seconds)


class TelegraphMember(models.Model):
    channel = models.ForeignKey(
        TelegraphChannel,
        on_delete=models.CASCADE,
        related_name="telegraph_members",
    )
    person = models.ForeignKey(
        "people.Person",
        on_delete=models.CASCADE,
        related_name="telegraph_memberships",
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
                name="unique_telegraph_channel_member",
            ),
            models.CheckConstraint(
                check=models.Q(person__isnull=False),
                name="telegraph_member_must_have_person",
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


class TelegraphMessage(models.Model):
    """A persisted relay ("Forum"/Discord) message.

    ``encryption="at_rest"`` rows are encrypted under the channel's gervazy DEK and the
    server CAN decrypt them (Discord model) to serve readable history to any member,
    including brand-new joiners. ``encryption="e2e"`` rows are *secure-on-send*: encrypted
    on the client under a member-held key the server cannot read (opaque ciphertext/iv/
    pin_key_id). In-transit confidentiality is TLS.
    """

    MSG_TYPES = [
        ("chat_message", "chat_message"),
        ("image_message", "image_message"),
        ("voice_message", "voice_message"),
    ]

    # at_rest: encrypted under the channel DEK; the server CAN read it (Discord history).
    # e2e:     "secure-on-send" — encrypted on the client under the member-held pin key;
    #          the server stores opaque ciphertext + iv + pin_key_id and CANNOT read it.
    ENCRYPTION_CHOICES = [("at_rest", "at_rest"), ("e2e", "e2e")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(
        TelegraphChannel, on_delete=models.CASCADE, related_name="messages"
    )
    encryption = models.CharField(max_length=16, choices=ENCRYPTION_CHOICES, default="at_rest")
    # e2e rows only: which member-held key encrypted it + its AES-GCM IV. (at_rest rows
    # use the DEK envelope below: nonce + aad.)
    pin_key_id = models.CharField(max_length=64, blank=True, default="")
    iv = models.BinaryField(null=True, blank=True)
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

    # AES-256-GCM (gervazy) of the message payload JSON. AAD binds the ciphertext to
    # ``channel.slug + str(id)`` so a row cannot be replayed under another identity.
    ciphertext = models.BinaryField()
    nonce = models.BinaryField()
    aad = models.BinaryField(default=bytes)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["channel", "created_at"])]

    def __str__(self):
        return f"{self.msg_type} in {self.channel.slug} @ {self.created_at:%Y-%m-%d %H:%M}"

    def is_expired(self, now=None):
        if not self.expires_at:
            return False
        return (now or timezone.now()) >= self.expires_at


