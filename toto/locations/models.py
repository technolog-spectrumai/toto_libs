from django.contrib.gis.db import models

from toto.core.domain import DomainEntity

SRID = 4326  # WGS84 (OpenStreetMap)


class Address(DomainEntity):
    country_name = models.CharField(max_length=2, verbose_name="Country")
    state_or_province_name = models.CharField(max_length=128, verbose_name="State/Province")
    locality_name = models.CharField(max_length=128, verbose_name="Locality")
    street = models.CharField(max_length=255, verbose_name="Street")
    building = models.CharField(max_length=64, verbose_name="Building Number")
    apartment = models.CharField(max_length=64, blank=True, null=True, verbose_name="Apartment Number")
    geometry = models.PointField(srid=SRID, null=True, blank=True)

    def __str__(self):
        base = f"{self.street} {self.building}"
        if self.apartment:
            base += f", Apt {self.apartment}"
        return f"{base}, {self.locality_name}, {self.state_or_province_name}, {self.country_name}"


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
    style = models.JSONField(
        default=dict,
        blank=True,
        help_text="Frontend style config: color scale, opacity, legend, etc.",
    )
    is_active = models.BooleanField(default=True)

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
