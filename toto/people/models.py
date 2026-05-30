from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from toto.core.domain import DomainEntity


class Person(DomainEntity):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="community_profile",
        null=True,
        blank=True,
    )
    communities = models.ManyToManyField(
        "socialhub.Community",
        related_name="members",
        blank=True,
        # Preserve the physical through-table that already exists in the DB.
        db_table="socialhub_person_communities",
    )
    patron = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mentees",
    )
    display_name = models.CharField(max_length=150)
    bio = models.TextField(null=True, blank=True)
    avatar = models.ImageField(upload_to="avatars/", null=True, blank=True)
    joined_date = models.DateTimeField(default=timezone.now)
    slug = models.SlugField(unique=True, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    address = models.ForeignKey(
        "locations.Address",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="residents",
        help_text="Optional address for this community member",
    )
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    is_federal_agent = models.BooleanField(
        default=False,
        help_text="Federal agents are exempt from community poll taxes.",
    )
    digital_signature = models.TextField(
        blank=True,
        help_text="Base64-encoded PNG of the person's handwritten signature.",
    )
    preferred_language = models.CharField(
        max_length=10,
        choices=settings.LANGUAGES,
        default="en",
        blank=True,
    )

    class Meta:
        # Keep the same physical table — zero DB migration needed.
        db_table = "socialhub_person"

    def __str__(self):
        return self.display_name

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.display_name)
            slug = base_slug
            counter = 1
            while Person.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def full_name(self):
        if self.display_name:
            return self.display_name
        if self.user:
            return self.user.get_full_name() or self.user.username
        return "Unnamed Member"
