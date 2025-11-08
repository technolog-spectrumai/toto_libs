from django.db import models
import uuid
from django.utils.timezone import now
from django.core.exceptions import ValidationError
from django.urls import reverse


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

    def __str__(self):
        return self.name

    @property
    def url(self):
        # Assuming you have a federation detail view named 'federation_detail'
        return reverse("federal:federation_detail", kwargs={"slug": self.slug})


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


