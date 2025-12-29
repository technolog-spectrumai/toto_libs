from django.db import models
from django.utils.text import slugify
from django.core.exceptions import ValidationError
from django_jsonform.models.fields import JSONField
from community.models import Persona      # your polymorphic base class
from locations.models import Address        # optional company HQ address


class Company(Persona):
    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(unique=True, blank=True)

    registration_number = models.CharField(
        max_length=100,
        unique=True,
        blank=True,
        null=True
    )

    founded_date = models.DateField(blank=True, null=True)
    website = models.URLField(blank=True, null=True)
    email = models.EmailField(blank=True, null=True)

    headquarters = models.ForeignKey(
        Address,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="company_headquarters",
        db_comment="located_in"
    )

    metadata = JSONField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)
            slug = base
            counter = 1
            while Company.objects.filter(slug=slug).exists():
                slug = f"{base}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)


class FractionalOwnership(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="ownerships_received"
    )
    # Any SocialEntity can be an owner (Company, Community, Member, etc.)
    owner_entity = models.ForeignKey(
        Persona,
        on_delete=models.CASCADE,
        related_name="ownerships_given"
    )

    percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text="Ownership percentage (e.g., 12.50)"
    )

    metadata = JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-percentage"]

    def clean(self):
        if self.percentage <= 0:
            raise ValidationError("Ownership percentage must be positive.")

        # Prevent self‑ownership only if owner is also a Company
        if (
            isinstance(self.owner_entity, Company)
            and self.owner_entity_id == self.owned_company_id
        ):
            raise ValidationError("A company cannot own itself.")

    def __str__(self):
        return f"{self.owner_entity} owns {self.percentage}% of {self.owned_company}"

