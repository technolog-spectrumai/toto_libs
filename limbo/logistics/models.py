# logistics
from django.db import models
from toto.core.domain import DomainEntity


class TransportMode(models.TextChoices):
    ROAD = "road", "Road"
    RAIL = "rail", "Rail"
    AIR = "air", "Air"
    SEA = "sea", "Sea"
    COURIER = "courier", "Courier"
    OTHER = "other", "Other"


class PackageStatus(models.TextChoices):
    CREATED = "created", "Created"
    PICKED_UP = "picked_up", "Picked up"
    IN_TRANSIT = "in_transit", "In transit"
    AT_HUB = "at_hub", "At hub"
    OUT_FOR_DELIVERY = "out_for_delivery", "Out for delivery"
    DELIVERED = "delivered", "Delivered"
    FAILED = "failed", "Failed"
    RETURNED = "returned", "Returned"


class Transport(DomainEntity):
    """
    A carrier / vehicle entity that moves packages between locations.
    A Transport has a mode (road, rail, air…) and optional current location.
    Packages are linked to a Transport while they are loaded on it.
    """
    name = models.CharField(max_length=255)
    carrier = models.CharField(max_length=255, blank=True)
    mode = models.CharField(max_length=20, choices=TransportMode.choices, default=TransportMode.ROAD)
    identifier = models.CharField(
        max_length=100, blank=True,
        help_text="Vehicle ID, flight number, vessel name, etc.",
    )
    current_location = models.ForeignKey(
        "locations.Address",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="transports_here",
    )
    origin = models.ForeignKey(
        "locations.Address",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="transports_from",
    )
    destination = models.ForeignKey(
        "locations.Address",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="transports_to",
    )
    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.get_mode_display()})"


class Package(DomainEntity):
    """A physical package being tracked through the logistics network."""
    tracking_number = models.CharField(max_length=100, unique=True)
    carrier = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=32, choices=PackageStatus.choices, default=PackageStatus.CREATED
    )
    transport = models.ForeignKey(
        Transport,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="packages",
        help_text="Current transport carrying this package",
    )
    # Optional link back to a bazaar shipment
    shipment = models.OneToOneField(
        "bazaar.Shipment",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="logistics_package",
    )
    origin = models.ForeignKey(
        "locations.Address",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="outbound_packages",
    )
    destination = models.ForeignKey(
        "locations.Address",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="inbound_packages",
    )
    description = models.TextField(blank=True)
    estimated_delivery = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.tracking_number

    @property
    def last_event(self):
        return self.events.order_by("-occurred_at").first()


class PackageEvent(DomainEntity):
    """A single tracking event in a package's journey."""
    package = models.ForeignKey(Package, on_delete=models.CASCADE, related_name="events")
    status = models.CharField(max_length=32, choices=PackageStatus.choices)
    transport = models.ForeignKey(
        Transport,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="events",
    )
    location = models.ForeignKey(
        "locations.Address",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="package_events",
    )
    note = models.TextField(blank=True)
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]

    def __str__(self):
        return f"{self.package.tracking_number} — {self.get_status_display()} @ {self.occurred_at:%Y-%m-%d %H:%M}"
