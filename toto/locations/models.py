from django.contrib.gis.db import models
from toto.core.domain import DomainEntity


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
    geometry = models.PointField(srid=SRID, null=True, blank=True)

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
    geometry = models.PolygonField(srid=SRID)
    capital = models.ForeignKey(
        Address,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="territory_capitals"
    )

    def __str__(self):
        return self.name


class Zone(DomainEntity):
    name = models.CharField(max_length=200)
    geometry = models.MultiPolygonField(srid=SRID)
    territory = models.ForeignKey(
        Territory,
        on_delete=models.CASCADE,
        related_name="zones",
        null=True,
        blank=True,
    )

    def __str__(self):
        return self.name


class RouteChain(DomainEntity):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


class Route(DomainEntity):
    name = models.CharField(max_length=200, blank=True)
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
        "socialhub.Person",
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


