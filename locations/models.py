# locations/models.py
from django.contrib.gis.db import models
from toto.models import SerializableModel


SRID = 4326  # WGS84 (OpenStreetMap)


class PointFeature(SerializableModel):
    name = models.CharField(max_length=200, blank=True)
    geometry = models.PointField(srid=SRID)

    def __str__(self):
        return self.name or f"Point {self.pk}"


class ZoneFeature(SerializableModel):
    name = models.CharField(max_length=200, blank=True)
    geometry = models.PolygonField(srid=SRID)

    def __str__(self):
        return self.name or f"Zone {self.pk}"


class PathFeature(SerializableModel):
    name = models.CharField(max_length=200, blank=True)
    geometry = models.MultiLineStringField(srid=SRID)

    def __str__(self):
        return self.name or f"Path {self.pk}"


class Address(SerializableModel):
    country_name = models.CharField(max_length=2, verbose_name="Country")
    state_or_province_name = models.CharField(max_length=128, verbose_name="State/Province")
    locality_name = models.CharField(max_length=128, verbose_name="Locality")
    street = models.CharField(max_length=255, verbose_name="Street")
    building = models.CharField(max_length=64, verbose_name="Building Number")
    apartment = models.CharField(max_length=64, verbose_name="Apartment Number", blank=True, null=True)
    geometry = models.PointField(srid=SRID, null=True, blank=True)

    def __str__(self):
        base = f"{self.street} {self.building}"
        if self.apartment:
            base += f", Apt {self.apartment}"
        return f"{base}, {self.locality_name}, {self.state_or_province_name}, {self.country_name}"