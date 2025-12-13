from django.urls import reverse
from oya.models import Platform
from gervazy.models import RSAKeyPair, SecretKey
import uuid
from django.db import models
import qrcode
import base64
from io import BytesIO
from django.utils.html import mark_safe
from django.contrib.auth.models import User
from locations.models import Address


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

    def __str__(self):
        return self.name

    @property
    def url(self):
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



