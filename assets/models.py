from django.db import models
from django.contrib.auth.models import User
from locations.models import Address
from django_jsonform.models.fields import JSONField


class AssetType(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Asset Type"
        verbose_name_plural = "Asset Types"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Asset(models.Model):
    name = models.CharField(max_length=255)
    asset_type = models.ForeignKey(AssetType, on_delete=models.CASCADE, related_name="assets", db_comment="is_type_of")
    description = models.TextField(blank=True, null=True)
    serial_number = models.CharField(max_length=100, unique=True, blank=True, null=True)
    purchase_date = models.DateField(blank=True, null=True)
    purchase_price = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, blank=True, null=True, db_comment="assigned_to")
    location = models.ForeignKey(Address, on_delete=models.SET_NULL, null=True, blank=True, db_comment="located_at")
    is_active = models.BooleanField(default=True)

    # 🔑 Flexible metadata field
    metadata = JSONField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.asset_type})"
