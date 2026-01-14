import uuid
from django.db import models
from django.urls import reverse
from django.contrib.auth.models import User
from oya.models import Platform
from gervazy.models import RSAKeyPair
from locations.models import Address
from django.core.exceptions import ValidationError


class Federation(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(
        max_length=100,
        unique=True,
        help_text="Unique slug identifier for this federation"
    )
    description = models.TextField(blank=True)
    logo = models.ImageField(
        upload_to='federation_logos/',
        null=True,
        blank=True,
        help_text="Optional logo for this federation"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=True)
    platform = models.OneToOneField(
        Platform,
        on_delete=models.CASCADE,
        related_name="federation",
        help_text="Platform associated with this federation"
    )
    location = models.ForeignKey(Address, on_delete=models.SET_NULL, null=True, blank=True)
    is_foreign = models.BooleanField(default=False,
                                     help_text="Indicates whether this federation originates outside the local jurisdiction")

    def __str__(self):
        return self.name

    @property
    def url(self):
        if not self.slug:
            return None
        return reverse("federal:federation_detail_json", kwargs={"slug": self.slug})



class IdentityProfile(models.Model):
    INDIVIDUAL = "individual"
    ORGANIZATION = "organization"

    PROFILE_TYPES = [
        (INDIVIDUAL, "Individual"),
        (ORGANIZATION, "Organization"),
    ]

    profile_type = models.CharField(
        max_length=20,
        choices=PROFILE_TYPES,
        default=INDIVIDUAL
    )

    # Optional links to system entities
    member = models.ForeignKey(
        "community.CommunityMember",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="identity_profiles"
    )

    community = models.ForeignKey(
        "community.Community",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="identity_profiles"
    )

    legal_name = models.CharField(max_length=255)
    registration_number = models.CharField(max_length=100, blank=True, null=True)
    registration_type = models.CharField(max_length=100, blank=True, null=True)

    is_verified = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        "community.CommunityMember",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_identity_profiles"
    )

    metadata = models.JSONField(blank=True, null=True, default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Identity Profile"
        verbose_name_plural = "Identity Profiles"

    def clean(self):
        # Prevent linking to both at once
        if self.member and self.community:
            raise ValidationError("IdentityProfile cannot reference both a member and a community.")

    def __str__(self):
        return f"{self.legal_name} ({self.profile_type})"

    @property
    def name(self):
        parts = [self.legal_name]
        if self.community:
            parts.append(f"Community: {self.community}")
        if self.member:
            parts.append(f"Member: {self.member}")
        return " | ".join(parts)


class FederatedIdentity(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    name = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="Human-readable label for this federated identity"
    )

    profile = models.OneToOneField(
        "IdentityProfile",
        on_delete=models.CASCADE,
        related_name="federated_identity",
        null=True,
        blank=True,
        help_text="IdentityProfile associated with this federated identity"
    )

    rsa_keypair = models.OneToOneField(
        RSAKeyPair,
        on_delete=models.CASCADE,
        related_name="federated_identity",
        null=True,
        blank=True,
        help_text="RSA keypair used for signing/verification"
    )

    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="federated_identities",
        help_text="Optional link to a Django User"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name or str(self.id)



