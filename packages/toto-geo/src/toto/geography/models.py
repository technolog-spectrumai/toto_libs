"""The two geographic types, and what ties them to people and communities.

``Address`` (a point) and ``Zone`` (one closed ring) are the only models of
the platform with a geometry column. Nothing here stores a line: a route is
calculated, answered and forgotten (``routing``).

ONE LINK PER GEOMETRY ROW, ALWAYS. An ``Address`` or a ``Zone`` belongs to
exactly one link row: a person's (``PersonAddress``) or a community's
(``CommunityHeadquarters``). Clearing, removing and erasing delete the
geometry row itself, not only its link (``saves``), and a ``post_delete``
receiver on every link row (``receivers``) does the same when the person or
the community goes.

toto-base keeps no geometry: a person's ``address`` and a community's
``seat`` stay the one postal text of each, and nothing here writes them.
"""

from django.conf import settings
from django.contrib.gis.db import models
from django.utils.translation import gettext_lazy as _

from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent

from .shapes import SRID


class Address(models.Model):
    """A point and what its owner calls it."""

    point = models.PointField(srid=SRID)
    name = models.CharField(max_length=200, blank=True)
    #: One text box. A person's and a community's postal text live on their
    #: own rows in toto-base; this is for a point that has no such row.
    postal_address = models.TextField(blank=True)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Address")
        verbose_name_plural = _("Addresses")

    def __str__(self):
        return self.name or f"Address {self.pk or ''}".strip()


class Zone(models.Model):
    """An area: one closed ring, valid and simple (``shapes.polygon_of``)."""

    name = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    outline = models.PolygonField(srid=SRID)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Zone")
        verbose_name_plural = _("Zones")

    def __str__(self):
        return self.name or f"Zone {self.pk or ''}".strip()


class PersonAddress(models.Model):
    """A person's own point. Who sees it is ``access.visible_point``."""

    person = models.OneToOneField(
        "people.Person", on_delete=models.CASCADE, related_name="geography_address")
    address = models.OneToOneField(
        Address, on_delete=models.CASCADE, related_name="person_link")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Person's address")
        verbose_name_plural = _("People's addresses")

    def __str__(self):
        return f"PersonAddress {self.pk or ''}".strip()


class CommunityHeadquarters(models.Model):
    """A community's headquarters point and its zone, each optional. The row
    goes when both are cleared."""

    community = models.OneToOneField(
        "socialhub.Community", on_delete=models.CASCADE,
        related_name="geography_headquarters")
    address = models.OneToOneField(
        Address, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="headquarters_link")
    zone = models.OneToOneField(
        Zone, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="headquarters_link")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Community headquarters")
        verbose_name_plural = _("Community headquarters")

    def __str__(self):
        return f"CommunityHeadquarters {self.pk or ''}".strip()


class GeographyUsageEvent(AbstractUsageEvent):
    """One charged search, route or save. ``metadata`` holds the request's
    keyed digest and nothing else (``charging``)."""

    class Meta(AbstractUsageEvent.Meta):
        verbose_name = _("Geography usage event")
        verbose_name_plural = _("Geography usage events")


class GeographyQuotaPolicy(AbstractQuotaPolicy):
    events = GeographyUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = _("Geography quota policy")
        verbose_name_plural = _("Geography quota policies")
