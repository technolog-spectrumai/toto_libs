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


class Travel(DomainEntity):
    participants = models.ManyToManyField(
        "socialhub.Person",
        related_name="travels",
        help_text="People participating in this travel"
    )

    route = models.ForeignKey(
        Route,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="travels",
        help_text="Route used for this travel"
    )

    info = models.TextField(
        blank=True,
        help_text="Additional information about the travel"
    )

    starts_at = models.DateTimeField(
        help_text="Travel start date and time"
    )

    ends_at = models.DateTimeField(
        help_text="Travel end date and time"
    )

    def __str__(self):
        route_name = self.route.name if self.route else "No route"
        return f"Travel via {route_name} from {self.starts_at} to {self.ends_at}"


class Visit(DomainEntity):
    participant = models.ForeignKey(
        "socialhub.Person",
        on_delete=models.CASCADE,
        related_name="visits",
        help_text="Person who made the visit"
    )

    location = models.ForeignKey(
        Address,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="visits",
        help_text="Location that was visited"
    )

    review = models.TextField(
        blank=True,
        null=True,
        help_text="Optional review text"
    )

    score = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Score from 1 to 5"
    )

    def __str__(self):
        return f"Visit by {self.participant} to {self.location or 'Unknown location'}"

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(score__gte=1, score__lte=5)
                    | models.Q(score__isnull=True)
                ),
                name="visit_score_between_1_and_5",
            )
        ]