import uuid
from pathlib import PurePosixPath

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
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

    # ── Room kinds (2026-09-25) ────────────────────────────────────────────
    # All four are fixed when the room is made — no room turns encrypted, and
    # a temporary room is not extended — except the password, which its owner
    # may change. Every row from before this change is an open, plaintext,
    # permanent room: the migration's defaults ARE the old behaviour.
    # See SECURITY.md.
    #: Who may join: anybody (open), whoever knows the password, or only
    #: whoever the owner or staff add (invite).
    access = models.CharField(max_length=8, choices=[
        ("open", _("Open")), ("password", _("Password")), ("invite", _("Invite only"))],
        default="open")
    #: Messages and attachments stored as AES-256-GCM ciphertext under the
    #: room's key (rooms.py), never as plaintext. Not searchable.
    is_encrypted = models.BooleanField(default=False)
    #: A temporary room: past this instant it refuses reads and sends, and the
    #: expiry sweep deletes it with everything in it. Its key lives only in the
    #: shared cache and expires with it.
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    #: The password is never stored. An Argon2id derivation over it yields a
    #: verifier (stored) and, for an encrypted room, a key-wrapping key (never
    #: stored) — rooms.derive_password_keys. The costs are per room, so tuning
    #: the default never locks an old room out.
    password_salt = models.BinaryField(null=True, blank=True, editable=False)
    password_verifier = models.BinaryField(null=True, blank=True, editable=False)
    kdf_memory_cost = models.PositiveIntegerField(null=True, blank=True, editable=False)
    kdf_iterations = models.PositiveIntegerField(null=True, blank=True, editable=False)
    kdf_lanes = models.PositiveIntegerField(null=True, blank=True, editable=False)

    @property
    def is_temporary(self) -> bool:
        return self.expires_at is not None

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= timezone.now()

    @property
    def has_password(self) -> bool:
        return self.access == "password"

    @property
    def is_invite_only(self) -> bool:
        return self.access == "invite"

    def badges(self) -> list[dict]:
        """What the UI says this room is, in the order it says it."""
        out = []
        if self.access == "password":
            out.append({"key": "password", "icon": "fa-key", "label": _("Password")})
        elif self.access == "invite":
            out.append({"key": "invite", "icon": "fa-user-lock", "label": _("Invite only")})
        else:
            out.append({"key": "open", "icon": "fa-door-open", "label": _("Open")})
        if self.is_encrypted:
            out.append({"key": "encrypted", "icon": "fa-lock", "label": _("Encrypted")})
        if self.is_temporary:
            out.append({"key": "temporary", "icon": "fa-hourglass-half",
                        "label": _("Temporary"), "until": self.expires_at})
        return out

    #: Slugs the forum's own URLs already own. `forum/urls.py` declares these
    #: BEFORE the `<slug:slug>/` catch-all, so a channel holding one would be
    #: permanently unreachable — its page would resolve to the forum's, not to
    #: the room. `create` and `search` have been shadowed since those routes
    #: existed and nothing stopped anybody; this closes that.
    #:
    #: Enforced on the MODEL rather than in the create view because the admin
    #: (which has `prepopulated_fields` and no validation) and
    #: `ingress_forum.py` both make channels without going near that view.
    RESERVED_SLUGS = frozenset({"create", "search", "cleanup", "export", "api"})

    #: How many rooms this platform will hold. A host raises it by setting
    #: `FORUM_MAX_CHANNELS`; there is no way to have none, because zero would
    #: make the app unusable rather than configurable.
    #:
    #: A CAP RATHER THAN A QUOTA, and the number is small on purpose: rooms
    #: are cheap to make and expensive to keep — each one carries a vault
    #: directory, a retention policy, a nightly cleanup pass and a websocket
    #: group, and a forum with fifty half-dead rooms is worse than one with
    #: eight live ones.
    DEFAULT_MAX_CHANNELS = 8

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @classmethod
    def max_channels(cls) -> int:
        from django.conf import settings

        return int(getattr(settings, "FORUM_MAX_CHANNELS",
                           cls.DEFAULT_MAX_CHANNELS))

    @classmethod
    def at_capacity(cls) -> bool:
        return cls.objects.count() >= cls.max_channels()

    def clean(self):
        super().clean()
        if self.access == "password" and not self.password_verifier:
            raise ValidationError(_("A password room needs a password."))
        if self.slug in self.RESERVED_SLUGS:
            raise ValidationError({
                "slug": _("“%(slug)s” is one of the forum's own addresses. "
                          "A room with that name could never be opened.")
                % {"slug": self.slug},
            })
        if self._state.adding and self.at_capacity():
            raise ValidationError(
                _("This platform holds at most %(n)s rooms, and it has that "
                  "many. Close one before opening another.")
                % {"n": self.max_channels()})

    def save(self, *args, **kwargs):
        """Enforced HERE as well as in `clean()`, and that is not belt and
        braces — it is the only place that actually runs.

        `clean()` is called by ModelForms and the admin; `objects.create()`
        never calls it. The admin, `ingress_forum.py` and any shell make
        channels without going near a form, which is exactly the reasoning
        `RESERVED_SLUGS` records for its own check being on the model. A cap
        that only the create view honoured would be a cap in name.
        """
        if self._state.adding and self.at_capacity():
            raise ValidationError(
                _("This platform holds at most %(n)s rooms, and it has that "
                  "many. Close one before opening another.")
                % {"n": self.max_channels()})
        super().save(*args, **kwargs)


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

    Messages do not expire on their own. A staff-set retention period
    removes older ones permanently — see `toto.forum.cleanup`, and note
    that it deletes the attachment bytes too, which a row delete does not.
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
    #: In an encrypted room the body is here, as `version || nonce || ct+tag`
    #: (sealing.py), and `body` stays empty — so search, the admin and any
    #: forgotten filter over `body` find nothing rather than ciphertext.
    body_sealed = models.BinaryField(null=True, blank=True, editable=False)
    #: The attachment bytes on disk are one sealed blob (same framing).
    attachment_sealed = models.BooleanField(default=False)

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

    @property
    def is_sealed(self) -> bool:
        return self.body_sealed is not None


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


# ---------------------------------------------------------------------------
# Retention
#
# For as long as this app has existed its own docstrings said the same thing —
# "messages are never expired automatically; retention is deferred work". These
# two models are that work: one dial staff set, and one row per sweep so the
# page can say what happened rather than guess.
#
# The dial lives in the database and NOT in settings, because the requirement
# is that staff choose it: a setting would need a redeploy and would put the
# number somewhere the page cannot write. There is deliberately no
# FORUM_RETENTION_DAYS "default" either — a second source of truth for one
# number is exactly how a dial and a deploy config drift apart.
# ---------------------------------------------------------------------------


class RunStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    RUNNING = "running", _("Running")
    SUCCESS = "success", _("Finished")
    PARTIAL = "partial", _("Stopped part-way")
    FAILED = "failed", _("Failed")


class TriggeredBy(models.TextChoices):
    BEAT = "beat", _("On schedule")
    MANUAL = "manual", _("Started by a person")
    EXPIRY = "expiry", _("A temporary room expired")


class ForumRetentionPolicy(models.Model):
    """How long the forum keeps what was said.

    ONE ROW PER CHANNEL, plus one with `channel=NULL` that is the platform
    default. That default row IS the singleton this model used to be — the same
    pk, the same dial, the same meaning — so an existing deployment keeps
    exactly the retention it had and gains the ability to say something
    different about one room.

    Resolution is `current(channel)`: the channel's own row if it has one, else
    the default. A channel row is created only when somebody sets one, so
    "most rooms follow the platform" costs no rows and, more importantly, means
    changing the platform dial still moves those rooms. A per-channel row is an
    OVERRIDE, and overriding is a thing you do on purpose.

    Staff only, everywhere. A retention period is a destruction schedule, and a
    room's own members must not be able to set one for each other — the same
    call every destructive surface on this platform makes.
    """

    channel = models.ForeignKey(
        "forum.ForumChannel", on_delete=models.CASCADE, null=True, blank=True,
        related_name="retention_policies",
        help_text=_("The room this covers. Empty means every room that has no "
                    "policy of its own."))

    #: Off on arrival, and this is not timidity. An app that begins destroying
    #: history the moment somebody installs it is a bug with a release note.
    #: The schedule may run every night from the day this ships; it will find
    #: `enabled` False and do nothing until a person turns it on.
    enabled = models.BooleanField(default=False)
    retention_days = models.PositiveIntegerField(
        default=365,
        validators=[MinValueValidator(1), MaxValueValidator(3650)],
        help_text=_("Messages older than this are permanently removed."))

    last_run_at = models.DateTimeField(null=True, blank=True)
    last_run_status = models.CharField(max_length=10, blank=True,
                                       choices=RunStatus.choices)
    last_error = models.TextField(blank=True)

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                   blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        verbose_name = _("forum retention policy")
        verbose_name_plural = _("forum retention policies")
        constraints = [
            # One override per channel. A partial constraint, because NULL is
            # the default row and SQL does not consider two NULLs equal — so a
            # plain UniqueConstraint on `channel` would permit any number of
            # default rows, which is the one thing that must not happen.
            models.UniqueConstraint(
                fields=["channel"], condition=models.Q(channel__isnull=False),
                name="forum_one_retention_policy_per_channel"),
        ]

    def __str__(self):
        state = _("on") if self.enabled else _("off")
        where = self.channel.name if self.channel_id else _("every room")
        return f"{where}: {self.retention_days} days ({state})"

    @property
    def is_default(self) -> bool:
        return self.channel_id is None

    @classmethod
    def default(cls):
        """The platform-wide row. pk=1 by construction, as it always was."""
        policy, _created = cls.objects.get_or_create(
            pk=1, defaults={"channel": None})
        return policy

    @classmethod
    def current(cls, channel=None):
        """The policy that governs this channel.

        The channel's own row if it has one, else the platform default. Note
        it does NOT create a channel row: a room without an override follows
        the platform, and it must keep following it when the platform dial
        moves.
        """
        if channel is not None:
            own = cls.objects.filter(channel=channel).first()
            if own is not None:
                return own
        return cls.default()

    @classmethod
    def for_channel(cls, channel):
        """The channel's OWN row, creating it from the default if absent.

        Only for the settings form — asking for one is what makes a room stop
        following the platform.
        """
        existing = cls.objects.filter(channel=channel).first()
        if existing is not None:
            return existing
        base = cls.default()
        return cls.objects.create(
            channel=channel, enabled=False,
            retention_days=base.retention_days)

    def boundary(self, now=None):
        """The cutoff: everything strictly older than this goes.

        THE one derivation. The page, the confirmation screen, the manual run
        and the scheduled task all call this and nothing else computes a
        cutoff — which is what lets the apply endpoint re-derive the boundary
        instead of trusting whatever the preview put in a form field.
        """
        from datetime import timedelta

        return (now or timezone.now()) - timedelta(days=self.retention_days)


class ForumCleanupRun(models.Model):
    """One sweep, and what it destroyed.

    The only record that an irreversible thing happened, which is why nothing
    — not the admin, not the person who started it — may delete one of these.

    NOTE for any future aggregate over this model: `Meta.ordering` folds into
    a GROUP BY, so every `.values().annotate()` needs a trailing `.order_by()`.
    """

    #: The room this sweep covered, or NULL for a forum-wide one. Recorded
    #: rather than derived, so a run's own row says what it was asked to do
    #: even after the channel is renamed — or deleted, which is why this is
    #: SET_NULL and not CASCADE: destroying a room must not erase the record
    #: that its history was destroyed.
    channel = models.ForeignKey(
        "forum.ForumChannel", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="cleanup_runs")
    channel_name = models.CharField(
        max_length=100, blank=True,
        help_text=_("The room's name as it was, kept for when the row is gone."))

    status = models.CharField(max_length=10, choices=RunStatus.choices,
                              default=RunStatus.PENDING, db_index=True)
    triggered_by = models.CharField(max_length=8, choices=TriggeredBy.choices,
                                    default=TriggeredBy.BEAT)
    triggered_by_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                          blank=True,
                                          on_delete=models.SET_NULL,
                                          related_name="+")

    #: The cutoff actually used, and the dial it came from — copied in rather
    #: than re-derived, so moving the dial tomorrow does not rewrite what
    #: yesterday's sweep says it did.
    boundary = models.DateTimeField()
    retention_days = models.PositiveIntegerField()

    messages_deleted = models.PositiveIntegerField(default=0)
    attachments_deleted = models.PositiveIntegerField(default=0)
    bytes_freed = models.PositiveBigIntegerField(default=0)
    #: Attachment rows whose bytes were already gone from disk. Counted rather
    #: than hidden: it is the visible size of the leak this sweep drains.
    blobs_missing = models.PositiveIntegerField(default=0)
    #: Replies whose quoted parent was removed. They keep their own text and
    #: lose the quote (`reply_to` is SET_NULL).
    replies_orphaned = models.PositiveIntegerField(default=0)
    #: Polls and the votes cast in them. Counted separately because deleting
    #: them is a decision this app reversed: the sweep used to spare a poll on
    #: the grounds that "a poll is a decision record, not a conversation", and
    #: retention is now an unconditional promise instead.
    polls_deleted = models.PositiveIntegerField(default=0)
    ballots_deleted = models.PositiveIntegerField(default=0)
    channels_touched = models.PositiveIntegerField(default=0)

    error = models.TextField(blank=True)
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["status", "started_at"])]

    def __str__(self):
        return f"{self.get_status_display()} — {self.messages_deleted} removed"

    @property
    def is_finished(self) -> bool:
        return self.status in (RunStatus.SUCCESS, RunStatus.PARTIAL,
                               RunStatus.FAILED)


class ForumRoomKey(models.Model):
    """The key of one persistent encrypted room, wrapped — never in clear.

    `platform_*` is the room key under the `forum-rooms` strongbox's data key
    (FORUM_VAULT_PASSWORD, minted by deploy.py): what lets the server read the
    room for its members, and what recovers it. `password_*` is the same room
    key under a key derived from the room password: what recovers a password
    room if the platform secret is ever lost. A temporary room has no row —
    its key lives only in the cache, and dies with it.
    """

    channel = models.OneToOneField(ForumChannel, on_delete=models.CASCADE,
                                   related_name="room_key")
    platform_wrapped = models.BinaryField()
    platform_nonce = models.BinaryField()
    platform_wrapped_key = models.ForeignKey("gervazy.WrappedDataKey",
                                             on_delete=models.PROTECT, related_name="+")
    password_wrapped = models.BinaryField(null=True, blank=True)
    password_nonce = models.BinaryField(null=True, blank=True)
    version = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    rotated_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"room key v{self.version} for {self.channel_id}"


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
