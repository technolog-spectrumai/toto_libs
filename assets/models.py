from django.db import models
from django.core.exceptions import ValidationError
from locations.models import Address
from community.models import CommunityMember, Community
from federal.models import IdentityProfile


# ---------------------------------------------------------
#  ASSET TYPE
# ---------------------------------------------------------

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


# ---------------------------------------------------------
#  ASSET
# ---------------------------------------------------------

class Asset(models.Model):
    name = models.CharField(max_length=255)
    asset_type = models.ForeignKey(
        AssetType,
        on_delete=models.CASCADE,
        related_name="assets",
        db_comment="is_type_of"
    )
    description = models.TextField(blank=True, null=True)
    serial_number = models.CharField(max_length=100, unique=True, blank=True, null=True)
    purchase_date = models.DateField(blank=True, null=True)
    purchase_price = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    assigned_to = models.ForeignKey(CommunityMember, on_delete=models.SET_NULL, blank=True, null=True)
    location = models.ForeignKey(Address, on_delete=models.SET_NULL, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(blank=True, null=True, default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.asset_type})"


# ---------------------------------------------------------
#  ASSET IMAGES
# ---------------------------------------------------------

class AssetImage(models.Model):
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="images"
    )
    image = models.ImageField(upload_to="assets/images/")
    caption = models.CharField(max_length=255, blank=True, null=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"Image for {self.asset.name}"


# ---------------------------------------------------------
#  FRACTIONAL OWNERSHIP
# ---------------------------------------------------------

class FractionalOwnership(models.Model):
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="fractional_owners"
    )

    owner = models.ForeignKey(
        IdentityProfile,
        on_delete=models.CASCADE,
        related_name="ownerships"
    )

    percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text="Ownership percentage (e.g., 12.50)"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["asset", "owner"]
        unique_together = ("asset", "owner")

    def clean(self):
        total = (
            FractionalOwnership.objects
            .filter(asset=self.asset)
            .exclude(pk=self.pk)
            .aggregate(models.Sum("percentage"))["percentage__sum"] or 0
        )

        if total + self.percentage > 100:
            raise ValidationError("Total ownership cannot exceed 100%.")

    def __str__(self):
        return f"{self.owner} owns {self.percentage}% of {self.asset}"
