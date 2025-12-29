from django.contrib.gis.db import models
from toto.models import SerializableModel

SRID = 4326  # WGS84 (OpenStreetMap)


class Address(SerializableModel):
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


class Province(SerializableModel):
    name = models.CharField(max_length=200)
    capital = models.ForeignKey(
        Address,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="province_capitals"
    )

    def __str__(self):
        return self.name


class Territory(SerializableModel):
    name = models.CharField(max_length=200)
    geometry = models.PolygonField(srid=SRID)
    capital = models.ForeignKey(
        Address,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="territory_capitals"
    )
    province = models.ForeignKey(
        Province,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="territories"
    )

    def __str__(self):
        return self.name


class Route(SerializableModel):
    name = models.CharField(max_length=200, blank=True)
    geometry = models.MultiLineStringField(srid=SRID)

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
