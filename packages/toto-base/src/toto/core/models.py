from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from colorfield.fields import ColorField
from django.contrib.auth import get_user_model
from django_jsonform.models.fields import JSONField
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

_THEME_EXTRA = {
    "type": "object",
    "properties": {
        "light": {"type": "string"},
        "dark": {"type": "string"}
    },
    "required": ["light", "dark"],
    "additionalProperties": False
}


class ColorMix(models.Model):
    name = models.CharField(
        max_length=64,
        unique=True,
        help_text="Name of the color mix"
    )

    # Light mode colors
    primary_bg_light = ColorField(default="#FFFFFF")
    header_bg_light = ColorField(default="#FFFFFF")
    appbar_bg_light = ColorField(default="#FFFFFF")
    appbar_text_light = ColorField(default="#000000")
    footer_bg_light = ColorField(default="#FFFFFF")
    footer_text_light = ColorField(default="#000000")
    bubble_bg_light = ColorField(default="#FFFFFF")
    text_main_light = ColorField(default="#000000")
    accent_light = ColorField(default="#FF4081")
    warn_light = ColorField(default="#FFC107")

    # NEW
    success_light = ColorField(default="#4CAF50")   # green
    sunken_light = ColorField(default="#F5F5F5")    # subtle grey
    link_light = ColorField(default="#1E88E5")      # blue
    # The security mana pool (2026-09-25): cyan, its own token so the bar can
    # never inherit the accent hue a theme happens to pick. `caution` was in
    # every theme file but never on this model, so the classes it produced
    # rendered colourless; now it is a field like the others.
    security_light = ColorField(default="#00ACC1")  # cyan
    caution_light = ColorField(default="#A07800")   # amber

    # Dark mode colors
    primary_bg_dark = ColorField(default="#121212")
    header_bg_dark = ColorField(default="#1F1F1F")
    appbar_bg_dark = ColorField(default="#1F1F1F")
    appbar_text_dark = ColorField(default="#FFFFFF")
    footer_bg_dark = ColorField(default="#1F1F1F")
    footer_text_dark = ColorField(default="#FFFFFF")
    bubble_bg_dark = ColorField(default="#2C2C2C")
    text_main_dark = ColorField(default="#FFFFFF")
    accent_dark = ColorField(default="#FF4081")
    warn_dark = ColorField(default="#FF5722")

    # NEW
    success_dark = ColorField(default="#66BB6A")    # lighter green for dark mode
    sunken_dark = ColorField(default="#1A1A1A")     # deeper grey
    link_dark = ColorField(default="#64B5F6")       # lighter blue
    security_dark = ColorField(default="#26C6DA")   # lighter cyan for dark mode
    caution_dark = ColorField(default="#F0A820")    # lighter amber

    # Accent colors
    accent_1 = ColorField(default="#03A9F4")
    accent_2 = ColorField(default="#4CAF50")

    def __str__(self):
        return self.name



class Theme(models.Model):

    name = models.CharField(
        max_length=64,
        unique=True,
        help_text="Name of the theme"
    )

    color_mix = models.ForeignKey(
        ColorMix,
        on_delete=models.CASCADE,
        related_name="themes",
        help_text="Color palette used for this theme"
    )

    font = models.ForeignKey(
        Font,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="themes",
        help_text="Font applied to this theme"
    )

    header = JSONField(
        schema=_THEME_EXTRA,
        default=dict,
        help_text="Tailwind classes for header element (light/dark mode)"
    )

    footer = JSONField(
        schema=_THEME_EXTRA,
        default=dict,
        help_text="Tailwind classes for footer element (light/dark mode)"
    )

    def __str__(self):
        return self.name

    @property
    def theme(self):
        colors = {}

        # Loop through all fields in ColorMix
        for field in self.color_mix._meta.get_fields():
            if hasattr(self.color_mix, field.name):
                value = getattr(self.color_mix, field.name)

                # Only include ColorField values (hex colors)
                if isinstance(value, str) and value.startswith("#"):
                    colors[field.name.replace("_", "-")] = value

        return {"colors": colors}


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

    theme = models.ForeignKey(
        Theme,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_themes",
        help_text="Theme applied to this platform",
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
    it stays in its secret file (toto.jess kept it in the database, and was
    retired for that).

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
