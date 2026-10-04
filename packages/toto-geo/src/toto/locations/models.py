from django.conf import settings
from django.utils.translation import gettext_lazy as _
from toto.core.domain import DomainEntity

# BUILD_GEO switch. When on (the default), locations is a GeoDjango app with
# real geometry columns backed by PostGIS/spatialite. When off, the models load
# with the plain ORM (no GDAL/GEOS), geometry fields are omitted, and Address
# carries plain lat/lon floats instead — see the suite README, "Making GIS
# optional". Hosts set HAS_GIS from features.geo.
HAS_GIS = getattr(settings, "HAS_GIS", True)

if HAS_GIS:
    from django.contrib.gis.db import models
else:
    from django.db import models


SRID = 4326  # WGS84 (OpenStreetMap)


class Address(DomainEntity):
    country_name = models.CharField(
        max_length=2,
        blank=True,
        verbose_name="Country",
    )
    state_or_province_name = models.CharField(
        max_length=128,
        blank=True,
        verbose_name="State/Province",
    )
    locality_name = models.CharField(
        max_length=128,
        blank=True,
        verbose_name="Locality",
    )
    street = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Street",
    )
    building = models.CharField(
        max_length=64,
        blank=True,
        verbose_name="Building Number",
    )
    apartment = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        verbose_name="Apartment Number",
    )
    # Canonical point coordinates — backend-agnostic, always present. On a
    # GIS build these mirror `geometry`; on a GIS-off build they are the only
    # coordinate store.
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    if HAS_GIS:
        geometry = models.PointField(srid=SRID, null=True, blank=True)
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Free-form JSON metadata for this location.",
    )
    note = models.TextField(
        blank=True,
        help_text="Free-text note about this address.",
    )
    #: Who created it (2026-09-25). Writes to metadata and notes are the
    #: creator's or staff's; a row from before this column is staff's alone.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+",
    )

    def save(self, *args, **kwargs):
        # Keep geometry and the lat/lon floats in sync on a GIS build. Geometry
        # is authoritative when set — the admin map widget, ingress and the map
        # API edit it directly, and the floats simply follow it. A coordinate
        # create (the address form sets lat/lon and no geometry) derives the
        # geometry from them. No-op on a GIS-off build.
        if HAS_GIS:
            if self.geometry is not None:
                self.longitude, self.latitude = self.geometry.x, self.geometry.y
            elif self.latitude is not None and self.longitude is not None:
                from django.contrib.gis.geos import Point
                self.geometry = Point(self.longitude, self.latitude, srid=SRID)
        super().save(*args, **kwargs)

    def __str__(self):
        parts = []

        street_part = " ".join(
            part for part in [self.street, self.building]
            if part
        ).strip()

        if street_part:
            parts.append(street_part)

        if self.apartment:
            parts.append(f"Apt {self.apartment}")

        if self.locality_name:
            parts.append(self.locality_name)

        if self.state_or_province_name:
            parts.append(self.state_or_province_name)

        if self.country_name:
            parts.append(self.country_name)

        if parts:
            return ", ".join(parts)

        return f"Address {self.pk or ''}".strip()


class Territory(DomainEntity):
    name = models.CharField(max_length=200)
    if HAS_GIS:
        geometry = models.PolygonField(srid=SRID)
    capital = models.ForeignKey(
        Address,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="territory_capitals"
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Free-form JSON metadata for this location.",
    )

    def __str__(self):
        return self.name


class Zone(DomainEntity):
    name = models.CharField(max_length=200)
    if HAS_GIS:
        geometry = models.MultiPolygonField(srid=SRID)
    territory = models.ForeignKey(
        Territory,
        on_delete=models.CASCADE,
        related_name="zones",
        null=True,
        blank=True,
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Free-form JSON metadata for this location.",
    )

    def __str__(self):
        return self.name


class RouteChain(DomainEntity):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Free-form JSON metadata for this location.",
    )

    def __str__(self):
        return self.name


class Route(DomainEntity):
    name = models.CharField(max_length=200, blank=True)
    if HAS_GIS:
        geometry = models.MultiLineStringField(srid=SRID)
    route_chain = models.ForeignKey(
        RouteChain,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="routes",
    )
    sequence = models.PositiveIntegerField(default=0)

    start_address = models.ForeignKey(
        Address,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="route_starts"
    )
    end_address = models.ForeignKey(
        Address,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="route_ends"
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Free-form JSON metadata for this location.",
    )
    notes = models.TextField(
        blank=True,
        help_text="Free-text notes about this route.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+",
    )

    def __str__(self):
        return self.name or f"Route {self.pk}"


class MapLayer(DomainEntity):
    """
    A map layer is a collection of continuous polygons.
    """

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    description = models.TextField(blank=True)
    unit = models.CharField(
        max_length=32,
        blank=True,
        help_text="Examples: °C, %, mm, ppm, people/km²",
    )
    min_value = models.FloatField(
        null=True,
        blank=True,
        help_text="Optional display minimum for color scaling.",
    )
    max_value = models.FloatField(
        null=True,
        blank=True,
        help_text="Optional display maximum for color scaling.",
    )
    style = models.JSONField(
        default=dict,
        blank=True,
        help_text="Frontend style config: color scale, opacity, legend, etc.",
    )
    inverted_importance = models.BooleanField(
        default=False,
        help_text="Invert value importance before color scaling.",
    )
    half_range = models.BooleanField(
        default=False,
        help_text="Use only the low-to-mid half of the color scale.",
    )
    is_active = models.BooleanField(default=True)
    owner = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_map_layers",
        help_text="Person who owns or manages this map layer"
    )


    def __str__(self):
        return self.name


class MapLayerPolygon(DomainEntity):
    """
    One continuous polygon inside a map layer.
    """

    layer = models.ForeignKey(
        MapLayer,
        on_delete=models.CASCADE,
        related_name="polygons",
    )
    name = models.CharField(max_length=200, blank=True)
    if HAS_GIS:
        geometry = models.PolygonField(
            srid=SRID,
            help_text="Must be one continuous polygon.",
        )
        center = models.PointField(
            srid=SRID,
            null=True,
            blank=True,
            help_text="Stored center point used for layer labels and markers.",
        )
    value = models.FloatField()
    properties = models.JSONField(
        default=dict,
        blank=True,
        help_text="Optional metadata for frontend/domain use.",
    )

    class Meta:
        indexes = [
            models.Index(fields=["layer"]),
        ]

    def __str__(self):
        return self.name or f"{self.layer.name} Polygon {self.pk}"




# ---------------------------------------------------------------------------
# Map domains (2026-09-30): clearances go on groups, never on items.
# ---------------------------------------------------------------------------


class MapDomain(models.Model):
    """A named group of map items — routes, map layers, addresses, zones,
    territories, and whatever an installed app adds (the host's places) —
    made and kept by superusers on the Domains tab.

    Clearances go on a domain, never on an item (`MapDomainClearance`). An
    item in no kept domain is every signed-in member's; an item in kept
    domains is read by superusers and by whoever holds, for EVERY kept domain
    of the item, one of that domain's clearances — not by its creator or owner
    (`access`, the rule is `toto.socialhub.clearance_access.group_gate`).

    Membership is one typed through table per kind (`RouteInDomain`, …;
    never a generic key), each reached from the domain as ``<kind>_rows``
    and from the item as ``domain_rows``.
    """

    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("name",)

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify

            base = slugify(self.name)[:120] or "domain"
            slug, n = base, 2
            while MapDomain.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug, n = f"{base}-{n}", n + 1
            self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class MapDomainClearance(models.Model):
    """One clearance keeping one domain. The clearance is PROTECTED: one that
    still keeps a domain cannot be deleted."""

    domain = models.ForeignKey(MapDomain, on_delete=models.CASCADE, related_name="clearance_rows")
    clearance = models.ForeignKey("socialhub.Clearance", on_delete=models.PROTECT,
                                  related_name="map_domain_rows")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["domain", "clearance"],
                                    name="locations_domain_clearance_once"),
        ]

    def __str__(self):
        return f"{self.domain.name} — {self.clearance.name}"


class RouteInDomain(models.Model):
    domain = models.ForeignKey(MapDomain, on_delete=models.CASCADE, related_name="route_rows")
    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name="domain_rows")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["domain", "route"], name="locations_route_in_domain_once"),
        ]

    def __str__(self):
        return f"{self.route} in {self.domain.name}"


class MapLayerInDomain(models.Model):
    domain = models.ForeignKey(MapDomain, on_delete=models.CASCADE, related_name="map_layer_rows")
    map_layer = models.ForeignKey(MapLayer, on_delete=models.CASCADE, related_name="domain_rows")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["domain", "map_layer"],
                                    name="locations_layer_in_domain_once"),
        ]

    def __str__(self):
        return f"{self.map_layer} in {self.domain.name}"


class AddressInDomain(models.Model):
    domain = models.ForeignKey(MapDomain, on_delete=models.CASCADE, related_name="address_rows")
    address = models.ForeignKey(Address, on_delete=models.CASCADE, related_name="domain_rows")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["domain", "address"],
                                    name="locations_address_in_domain_once"),
        ]

    def __str__(self):
        return f"{self.address} in {self.domain.name}"


class ZoneInDomain(models.Model):
    domain = models.ForeignKey(MapDomain, on_delete=models.CASCADE, related_name="zone_rows")
    zone = models.ForeignKey(Zone, on_delete=models.CASCADE, related_name="domain_rows")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["domain", "zone"], name="locations_zone_in_domain_once"),
        ]

    def __str__(self):
        return f"{self.zone} in {self.domain.name}"


class TerritoryInDomain(models.Model):
    domain = models.ForeignKey(MapDomain, on_delete=models.CASCADE, related_name="territory_rows")
    territory = models.ForeignKey(Territory, on_delete=models.CASCADE, related_name="domain_rows")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["domain", "territory"],
                                    name="locations_territory_in_domain_once"),
        ]

    def __str__(self):
        return f"{self.territory} in {self.domain.name}"


# ---------------------------------------------------------------------------
# What ties the map to people, events and communities (2026-10-04)
# ---------------------------------------------------------------------------
# Until 2026-10-04 these were keys ON toto-base's models: Person.address and
# Person.location_sharing, ScheduledEvent.address, Community.location and
# Community.territory. toto-base carries no geography now (a person's address,
# an event's place and a community's seat are text there), and this app moved
# to toto-geo; so the links live here, one row per person, event or community,
# and a host without this app has none of them.


class HomeSharing(models.TextChoices):
    """How much of a person's whereabouts other members may see (it was
    ``toto.people.models.LocationSharing``).

    The order is deliberate: OFF first, so it is the default any new column,
    any fixture and any forgotten argument lands on.
    """

    OFF = "off", _("Not shown to anyone")
    APPROXIMATE = "approximate", _("Approximate area only")
    EXACT = "exact", _("Exact address")


class Home(models.Model):
    """Where a person lives on the map, and whether others may see it.

    OFF is the default and that is the whole point: a home location is the
    most sensitive thing this app stores, so appearing on the People map is
    something a person switches ON. One field rather than a boolean plus a
    precision, because the pair can express "sharing, precision unset" and
    this cannot. The rule is ``people_access``.
    """

    person = models.OneToOneField("people.Person", on_delete=models.CASCADE,
                                  related_name="home")
    address = models.ForeignKey(Address, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="homes")
    sharing = models.CharField(max_length=12, choices=HomeSharing.choices,
                               default=HomeSharing.OFF)

    def __str__(self):
        return f"{self.person} at {self.address}"


class EventPlace(models.Model):
    """The map address an event takes place at (the event's own ``address``
    is text)."""

    event = models.OneToOneField("events.ScheduledEvent", on_delete=models.CASCADE,
                                 related_name="place_on_map")
    address = models.ForeignKey(Address, on_delete=models.CASCADE, related_name="event_places")

    def __str__(self):
        return f"{self.event} at {self.address}"


class CommunitySeat(models.Model):
    """A community's seat on the map and the territory it covers (the
    community's own ``seat`` is text)."""

    community = models.OneToOneField("socialhub.Community", on_delete=models.CASCADE,
                                     related_name="seat_on_map")
    address = models.ForeignKey(Address, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="community_seats")
    territory = models.ForeignKey(Territory, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name="community_seats")

    def __str__(self):
        return f"{self.community} at {self.address}"


# Metering (2026-09-28): server-side geocoding is charged per lookup, and the
# pair lives here so its rows go away with the app. Plain columns only — the
# same tables on a GIS and a GIS-off build (toto/locations/geocoding.py).
from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent  # noqa: E402


class LocationsUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        verbose_name = "Locations usage event"
        verbose_name_plural = "Locations usage events"


class LocationsQuotaPolicy(AbstractQuotaPolicy):
    events = LocationsUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = "Locations quota policy"
        verbose_name_plural = "Locations quota policies"
