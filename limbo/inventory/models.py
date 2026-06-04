from django.db import models
from django.utils.text import slugify
from toto.core.domain import DomainEntity


def unique_slug_for(instance, field_value, *, scope=None, slug_field="slug"):
    base = slugify(field_value) or instance.__class__.__name__.lower()
    slug = base
    counter = 1
    qs = instance.__class__.objects.all()
    if scope:
        qs = qs.filter(**scope)
    while qs.filter(**{slug_field: slug}).exclude(pk=instance.pk).exists():
        counter += 1
        slug = f"{base}-{counter}"
    return slug


class ObjectCategory(models.TextChoices):
    COMMODITY      = "commodity",      "Commodity"
    EQUIPMENT      = "equipment",      "Equipment"
    VEHICLE        = "vehicle",        "Vehicle"
    REAL_ESTATE    = "real_estate",    "Real Estate"
    INFRASTRUCTURE = "infrastructure", "Infrastructure"
    OTHER          = "other",          "Other"


class ObjectType(DomainEntity):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=280, unique=True, blank=True)
    category = models.CharField(
        max_length=30,
        choices=ObjectCategory.choices,
        blank=True,
        db_index=True,
    )
    is_mobile = models.BooleanField(
        default=False,
        help_text="Objects of this type move (vehicles, portable equipment). Used for fleet map filtering.",
    )
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["slug"]),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug_for(self, self.name)
        super().save(*args, **kwargs)


class RealWorldObject(DomainEntity):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=280, unique=True, blank=True)

    object_type = models.ForeignKey(
        ObjectType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="typed_objects",
    )

    description = models.TextField(blank=True)

    owner = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_inventory_objects",
    )

    custodian = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="custodied_inventory_objects",
    )

    verified_by = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_inventory_objects",
    )

    location = models.ForeignKey(
        "locations.Address",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_objects",
    )

    quantity = models.DecimalField(
        max_digits=24,
        decimal_places=8,
        null=True,
        blank=True,
    )

    unit = models.CharField(max_length=32, blank=True)

    estimated_value = models.DecimalField(
        max_digits=24,
        decimal_places=8,
        null=True,
        blank=True,
    )

    valuation_date = models.DateField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["object_type"]),
            models.Index(fields=["owner"]),
            models.Index(fields=["custodian"]),
            models.Index(fields=["verified_by"]),
            models.Index(fields=["verified_at"]),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug_for(self, self.name)
        super().save(*args, **kwargs)


class InventorySite(DomainEntity):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=280, unique=True, blank=True)

    site_type = models.CharField(
        max_length=64,
        blank=True,
        help_text="warehouse, vault, office, vehicle, partner, custodian, virtual, etc.",
    )

    operator = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="operated_inventory_sites",
    )

    address = models.ForeignKey(
        "locations.Address",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_sites",
    )

    is_active = models.BooleanField(default=True)
    is_virtual = models.BooleanField(
        default=False,
        help_text="Virtual sites have no physical location (e.g. cloud storage, in-transit, write-off pool).",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["site_type"]),
            models.Index(fields=["operator"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["is_virtual"]),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug_for(self, self.name)
        super().save(*args, **kwargs)


class StorageLocation(DomainEntity):
    site = models.ForeignKey(
        InventorySite,
        on_delete=models.CASCADE,
        related_name="storage_locations",
    )

    name = models.CharField(max_length=255)
    code = models.CharField(max_length=100, blank=True)

    location_type = models.CharField(
        max_length=64,
        blank=True,
        help_text="room, aisle, rack, shelf, bin, safe, freezer, virtual, etc.",
    )

    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["site", "code", "name"]
        unique_together = [("site", "code")]
        indexes = [
            models.Index(fields=["site", "code"]),
            models.Index(fields=["location_type"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return f"{self.site} / {self.code or self.name}"
