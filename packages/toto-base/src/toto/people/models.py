from functools import lru_cache
from zoneinfo import available_timezones

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from toto.core.domain import DomainEntity


@lru_cache(maxsize=1)
def time_zone_names() -> frozenset:
    """Every IANA zone this Python knows. Read once: the tz database does not
    change under a running process, and walking it costs a directory scan."""
    return frozenset(available_timezones())


def validate_time_zone(value):
    """An IANA name ("Europe/Warsaw"), or blank for the platform's default.

    Checked against zoneinfo rather than a list of our own, so the choices are
    exactly the zones the middleware can activate (2026-09-30).
    """
    if value and value not in time_zone_names():
        raise ValidationError(_("%(zone)s is not a known time zone."),
                              params={"zone": value}, code="invalid_time_zone")


class Person(DomainEntity):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="community_profile",
        null=True,
        blank=True,
    )
    communities = models.ManyToManyField(
        "socialhub.Community",
        related_name="members",
        blank=True,
        # Preserve the physical through-table that already exists in the DB.
        db_table="socialhub_person_communities",
    )
    #: The trust axis (2026-09-29): what this person may read, and how fast
    #: their mana refills — orthogonal to ``communities`` on purpose
    #: (socialhub README). Only a superuser changes it.
    clearances = models.ManyToManyField(
        "socialhub.Clearance",
        related_name="members",
        blank=True,
    )
    patron = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mentees",
    )
    display_name = models.CharField(max_length=150)
    bio = models.TextField(null=True, blank=True)
    avatar = models.ImageField(upload_to="avatars/", null=True, blank=True)
    joined_date = models.DateTimeField(default=timezone.now)
    slug = models.SlugField(unique=True, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    #: Where this person lives or can be reached by post: plain text, as they
    #: typed it (2026-10-04). It was a key to a map address with a pin and a
    #: three-way sharing switch; toto-base carries no geography now, so nothing
    #: here is looked up, geocoded or drawn. Who reads it is `show_address`.
    address = models.TextField(
        blank=True,
        default="",
        help_text="The postal address this person typed, as text. Nothing is looked up.",
    )
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    #: Whether other members see the e-mail address, the phone number and the
    #: postal address (2026-10-01, 37c.25; the address since 2026-10-04).
    #: Every member saw every other member's e-mail address and had no way to
    #: hide it; now each is the member's own choice, OFF by default. The
    #: member always sees their own and an administrator keeps seeing them
    #: (`toto.socialhub.contact_access`). On Person, beside the values they
    #: guard, because `datalink` replicates Person between federated hosts: a
    #: choice that did not travel with the person could be ignored by a host
    #: the person is copied to.
    show_email = models.BooleanField(
        default=False,
        help_text="Whether other members see this person's e-mail address. Off by default.",
    )
    show_phone = models.BooleanField(
        default=False,
        help_text="Whether other members see this person's phone number. Off by default.",
    )
    show_address = models.BooleanField(
        default=False,
        help_text="Whether other members see this person's postal address. Off by default.",
    )
    #: Whether the members of this person's communities are told when they
    #: sign in and out (2026-10-04, toto.notify's presence). On by default;
    #: theirs to switch off on the Edit profile tab. Nothing is kept either
    #: way: there is no list of who is online.
    show_online = models.BooleanField(
        default=True,
        help_text="Whether members of this person's communities are told when "
                  "they sign in and out. On by default.",
    )
    digital_signature = models.TextField(
        blank=True,
        help_text="Base64-encoded PNG of the person's handwritten signature.",
    )
    preferred_language = models.CharField(
        max_length=10,
        choices=settings.LANGUAGES,
        default="en",
        blank=True,
    )
    federated_sub = models.CharField(
        max_length=255,
        blank=True,
        db_index=True,
        help_text=(
            "OIDC subject of the identity provider this person was provisioned "
            "from, on a consumer host. Empty on the provider itself."
        ),
    )
    #: The member's own time zone (2026-09-30), activated on every request by
    #: `toto.core.middleware.ProfileTimezoneMiddleware`, so every page shows
    #: times where the member lives. Blank means the platform's TIME_ZONE.
    #: Declared last: the class body reads the module `timezone` above, and a
    #: field of that name must not shadow it for the fields before.
    timezone = models.CharField(
        max_length=64,
        blank=True,
        default="",
        validators=[validate_time_zone],
        help_text="IANA time zone name, e.g. Europe/Warsaw. Blank: the platform default.",
    )

    class Meta:
        # Keep the same physical table — zero DB migration needed.
        db_table = "socialhub_person"

    def __str__(self):
        return self.display_name

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.display_name)
            slug = base_slug
            counter = 1
            while Person.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def full_name(self):
        if self.display_name:
            return self.display_name
        if self.user:
            return self.user.get_full_name() or self.user.username
        return "Unnamed Member"
