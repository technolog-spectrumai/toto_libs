# locations/models.py
from django.contrib.gis.db import models


SRID = 4326  # WGS84 (OpenStreetMap)


class PointFeature(models.Model):
    name = models.CharField(max_length=200, blank=True)
    geometry = models.PointField(srid=SRID)

    def __str__(self):
        return self.name or f"Point {self.pk}"


class ZoneFeature(models.Model):
    name = models.CharField(max_length=200, blank=True)
    geometry = models.PolygonField(srid=SRID)

    def __str__(self):
        return self.name or f"Zone {self.pk}"


class PathFeature(models.Model):
    name = models.CharField(max_length=200, blank=True)
    geometry = models.MultiLineStringField(srid=SRID)

    def __str__(self):
        return self.name or f"Path {self.pk}"