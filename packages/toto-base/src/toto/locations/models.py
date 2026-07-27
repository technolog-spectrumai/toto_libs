from django.conf import settings
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


