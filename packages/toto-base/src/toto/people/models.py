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


class LocationSharing(models.TextChoices):
    """How much of a person's whereabouts other members may see.

    The order is deliberate: OFF first, so it is the default any new column,
    any fixture and any forgotten argument lands on.
    """

    OFF = "off", _("Not shown to anyone")
    APPROXIMATE = "approximate", _("Approximate area only")
    EXACT = "exact", _("Exact address")


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
    address = models.ForeignKey(
        "locations.Address",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="residents",
        help_text="Optional address for this community member",
    )
    #: Whether this person's address may be shown to other members, and how
    #: precisely. OFF is the default and that is the whole point: a home
    #: location is the most sensitive thing this platform stores, so appearing
    #: on the People map is something a person switches ON, never something
    #: they have to discover and switch off.
    #:
    #: ONE field rather than a boolean plus a precision, because the pair can
    #: express "sharing, precision unset" and this cannot.
    #:
    #: It lives on Person rather than in a Group or a side table because
    #: `datalink` replicates Person between federated hosts and REFUSES
    #: auth.Group ("group membership is a local authorization decision" —
    #: datalink_policies.py). A consent flag that did not travel with the person
    #: would let a federated host republish an address its owner switched off
    #: here.
    #: The choices, reachable from a template — Django templates cannot call
    #: `LocationSharing.choices` and iterating a hardcoded list in the markup is
    #: how the page and the field drift apart.
    LOCATION_SHARING_CHOICES = LocationSharing.choices

    location_sharing = models.CharField(
        max_length=12,
        choices=LocationSharing.choices,
        default=LocationSharing.OFF,
        help_text=(
            "Whether other members may see where this person lives, and how "
            "precisely. Off by default."
        ),
    )
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    is_federal_agent = models.BooleanField(
        default=False,
        help_text=(
            "DEPRECATED as a live flag — superseded by community privileges. "
            "It always meant two things at once: the old help text claimed an "
            "exemption from poll taxes (never implemented), while the code "
            "used it as a cross-community admin permission. Both live on the "
            "seeded 'Federal Agents' community now (CommunityPrivilege), and "
            "every holder of this flag was admitted to it by migration. Kept "
            "only because aurelian's mobilization templates read it; retiring "
            "it is an aurelian follow-up."
        ),
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
