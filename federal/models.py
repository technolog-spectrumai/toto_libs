import uuid
import base64
import qrcode
from io import BytesIO
from django.db import models
from django.urls import reverse
from django.utils.html import mark_safe
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


class FederatedIdentity(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )
    name = models.CharField(max_length=100, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    federation = models.ForeignKey(
        "Federation",
        on_delete=models.PROTECT,
        related_name="federated_identities",
        help_text="Federation this identity belongs to"
    )
    rsa_keypair = models.OneToOneField(   # 🔐 link to RSAKeyPair
        RSAKeyPair,
        on_delete=models.CASCADE,
        related_name="identity",
        null=True,
        blank=True,
        help_text="RSA keypair associated with this identity"
    )
    user = models.ForeignKey(  # 👤 optional link to Django User
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="federated_identities",
        help_text="Optional link to a Django User account"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["id", "federation"],
                name="unique_id_federation"
            )
        ]

    @property
    def issuer(self):
        return self.federation.url

    def __str__(self):
        return f"{self.name or self.id} from {self.issuer}"


    def qr_code(self):
        """
        Generate a QR code for this identity.
        By default, encode the issuer URL + UUID.
        """
        data = f"{self.issuer}/{self.id}"
        qr = qrcode.QRCode(box_size=6, border=2)
        qr.add_data(data)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        img_str = base64.b64encode(buffer.getvalue()).decode()

        return mark_safe(f'<img src="data:image/png;base64,{img_str}" />')


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





