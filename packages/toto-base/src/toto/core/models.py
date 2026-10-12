from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.contrib.auth import get_user_model
from toto.core.domain import DomainEntity

User = get_user_model()


class Font(models.Model):
    FONT_FAMILY_CHOICES = [
        ("serif", "Serif"),
        ("sans-serif", "Sans-serif"),
        ("monospace", "Monospace"),
        ("display", "Display / Decorative"),
    ]

    name = models.CharField(
        max_length=64,
        unique=True,
        help_text="Display name of the font (e.g. 'Playfair Display')"
    )

    cdn_link = models.URLField(
        help_text="CDN or stylesheet URL for importing the font (e.g. Google Fonts)"
    )

    style_family = models.CharField(
        max_length=20,
        choices=FONT_FAMILY_CHOICES,
        default="sans-serif",
        help_text="Font style family classification"
    )

    def __str__(self):
        return f"{self.name} ({self.style_family})"

# No ColorMix and no Theme since 2026-10-10. The colours were a row a host
# seeded from its own copy of a theme file at every start; there is one
# palette now, palette.json beside this module (toto/core/palette.py), read
# when the image is built. What stays in the database is the font, which has
# a stylesheet link of its own: Platform.font.


class Federation(DomainEntity):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    logo = models.ImageField(
        upload_to='federation_logos/',
        null=True,
        blank=True,
        help_text="Optional logo for this federation"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Platform(models.Model):
    domain = models.CharField(max_length=255, null=True, blank=True)
    site_name = models.CharField(max_length=255)
    author = models.CharField(max_length=255)
    publication_year = models.IntegerField()
    active = models.BooleanField(default=True)

    font = models.ForeignKey(
        Font,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platforms",
        help_text="Font this platform's pages are set in",
    )
    rate_limit_window = models.IntegerField(
        default=60,
        help_text="Rate limit window in seconds",
    )
    rate_limit_max_requests = models.IntegerField(
        default=20,
        help_text="Max requests allowed per window",
    )
    logo = models.ImageField(
        upload_to="federation_logos/",
        null=True,
        blank=True,
        help_text="Optional logo for this platform",
    )
    federation = models.ForeignKey(
        Federation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_federations",
        help_text="Federation this platform belongs to",
    )
    api_url = models.URLField(
        null=True,
        blank=True,
        help_text="Base API endpoint for this platform (e.g. https://example.com/api/)",
    )
    api_owner = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_api_accounts",
        help_text="User account used for API authentication",
    )
    def __str__(self):
        return f"{self.site_name} Platform"


class BootstrapMarker(models.Model):
    """What a start-up step remembers from one start to the next (stage 51).

    ``init_data`` runs on every container start. It used to set the admin's
    password from ADMIN_PASSWORD each time, so a password changed on the
    profile came back as the configured one at the next restart. It now keeps
    here a salted hash of the configured password it last applied
    (``admin-password:<username>``) and sets it again only when the
    configuration changes. Never the password itself.
    """

    name = models.CharField(max_length=150, unique=True)
    value = models.CharField(max_length=255, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class UserSession(models.Model):
    """One sign-in of a member: the row Django's session table does not have
    (2026-09-30).

    ``django_session`` has no user column, so "your sessions" and "sign out
    everywhere else" had nothing to list but a scan of every session on the
    host. A row is written when a session signs in (``user_logged_in``, a
    desktop token included — a token IS a session key), refreshed at most
    every few minutes while it is used, and deleted when it signs out or is
    ended from My account. See ``toto.core.user_sessions``.

    ``session_key`` is the key itself, not a hash of it: ending a session
    means deleting it from the session store, and only the key finds it
    there. It is no new exposure — the same key is the primary key of
    ``django_session`` in the same database and the same backups — but it
    is a credential all the same: it is never rendered, logged or put on the
    audit chain; the page names a session by this row's id.

    A row can outlive its session (expired, cleared, re-keyed): whatever
    lists them asks the session store first and drops the dead ones.
    """

    KIND_BROWSER = "browser"
    KIND_TOKEN = "token"
    KIND_CHOICES = [
        (KIND_BROWSER, _("Browser")),
        (KIND_TOKEN, _("Desktop or API token")),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="signed_in_sessions")
    session_key = models.CharField(max_length=40, unique=True)
    kind = models.CharField(max_length=16, choices=KIND_CHOICES, default=KIND_BROWSER)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-last_seen_at"]
        indexes = [models.Index(fields=["user", "-last_seen_at"],
                                name="core_usersession_user_seen")]

    def __str__(self):
        return f"{self.user_id} {self.kind} {self.created_at:%Y-%m-%d %H:%M}"


class KnownSignIn(models.Model):
    """A (user agent, address) pair a member signed in from, for the "new
    sign-in" notice (2026-09-30).

    Only a hash of the pair: this table answers "seen before?" for 90 days
    and nothing else, so it keeps no history of where a member has been.
    Its own table because a ``UserSession`` is deleted at sign-out, and a
    member who signs out every evening is not "new" every morning.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="known_sign_ins")
    fingerprint = models.CharField(max_length=64)
    last_seen_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "fingerprint"],
                                               name="core_knownsignin_unique_pair")]


class NoticeDelivery(models.Model):
    """How the last notice of one kind fared on its way out (2026-10-01).

    One row per kind (``toto.core.notices`` — a member's security notice or
    an operator's alert), overwritten by every try: NOT an outbox. No message
    is kept, no subject, no body, no link; the recipient only as a keyed hash
    (``notices.recipient_hash``: HMAC under SECRET_KEY, so a copy of this
    table cannot be matched against a list of addresses, while this host can
    still tell whether the last one went to a given address); the error as
    its class and SMTP reply code, never the server's own words, which can
    echo the address or the login. The SMTP password is nowhere near it —
    only the processes that send are given it (toto.jess kept it in the
    database, and was retired for that).

    ``failures`` counts the sends of this kind that failed for good since a
    notice of ANY kind last went: a delivery sets every row back to 0, so
    the sum over the table is how many sends in a row have failed — what
    the monitoring's Mail check (``toto.monit.record.check_mail``) reads.
    """

    SENT = "sent"
    RETRYING = "retrying"
    FAILED = "failed"
    STATUS_CHOICES = [
        (SENT, _("Sent")),
        (RETRYING, _("Being retried")),
        (FAILED, _("Failed")),
    ]

    #: The notice kind (``notices.KINDS``), also its mail's X-Toto-Notice.
    purpose = models.CharField(max_length=40, unique=True)
    recipient_hash = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    #: The tries the last send has had: 1, or up to ``notices.TRIES``.
    tries = models.PositiveSmallIntegerField(default=1)
    error = models.CharField(max_length=200, blank=True)
    failures = models.PositiveIntegerField(default=0)
    #: When this outcome was recorded.
    updated_at = models.DateTimeField(default=timezone.now)
    #: When a notice of this kind last went.
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["purpose"]
        verbose_name_plural = "notice deliveries"

    def __str__(self):
        return f"{self.purpose}: {self.status}"
