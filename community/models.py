from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.text import slugify
from django.utils.timezone import now
from django.core.exceptions import ValidationError
from yamabiko.models import SerializableModel
from polymorphic.models import PolymorphicModel
from federal.models import Federation


class Address(SerializableModel):
    country_name = models.CharField(max_length=2, verbose_name="Country")
    state_or_province_name = models.CharField(max_length=128, verbose_name="State/Province")
    locality_name = models.CharField(max_length=128, verbose_name="Locality")
    street = models.CharField(max_length=255, verbose_name="Street")
    building = models.CharField(max_length=64, verbose_name="Building Number")
    apartment = models.CharField(max_length=64, verbose_name="Apartment Number", blank=True, null=True)

    def __str__(self):
        base = f"{self.street} {self.building}"
        if self.apartment:
            base += f", Apt {self.apartment}"
        return f"{base}, {self.locality_name}, {self.state_or_province_name}, {self.country_name}"


class SocialEntity(SerializableModel, PolymorphicModel):
    created_at = models.DateTimeField(default=now)

    def clean(self):
        linked_models = []
        if hasattr(self, 'community'):
            linked_models.append('Community')
        if hasattr(self, 'member'):
            linked_models.append('CommunityMember')
        if len(linked_models) > 1:
            raise ValidationError(f"SocialEntity cannot be linked to multiple entities: {', '.join(linked_models)}")

    def __str__(self):
        return self.name or str(self.id)

    def is_community(self):
        return hasattr(self, "community")

    def is_member(self):
        return hasattr(self, "member")

    def is_company(self):
        return hasattr(self, "company")

    def get_identity_type(self):
        if self.is_community():
            return "Community"
        elif self.is_member():
            return "CommunityMember"
        elif self.is_company():
            return "Company"
        return "Unknown"

class Community(SocialEntity):
    name = models.CharField(max_length=255, help_text="Name of the community or organization")
    slug = models.SlugField(unique=True, blank=True, help_text="URL-friendly identifier")
    address = models.ForeignKey(Address, on_delete=models.SET_NULL, null=True, blank=True)
    established_year = models.IntegerField(null=True, blank=True)
    head = models.ForeignKey(
        'CommunityMember',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='headed_communities'
    )
    federation = models.ForeignKey(
        Federation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='communities'
    )
    updated_at = models.DateTimeField(auto_now=True)
    email = models.EmailField(unique=True, blank=True, null=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name)
            slug = base_slug
            counter = 1
            while Community.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)


class CommunityMember(SocialEntity):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='community_profile')
    communities = models.ManyToManyField(Community, related_name='members')
    patron = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='mentees'
    )
    display_name = models.CharField(max_length=150)
    bio = models.TextField(null=True, blank=True)
    avatar = models.ImageField(upload_to='avatars/', null=True, blank=True)
    joined_date = models.DateTimeField(default=timezone.now)
    slug = models.SlugField(unique=True, blank=True)

    def __str__(self):
        return self.display_name

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.display_name)
            slug = base_slug
            counter = 1
            while CommunityMember.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)


def generate_code(k=6):
    return ''.join(random.choices('0123456789', k=k))


class MembershipApplication(models.Model):
    email = models.EmailField(unique=True)
    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name='applications')
    code = models.CharField(max_length=10, unique=True, default=generate_code)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('verified', 'Verified'),
        ('endorsed', 'Endorsed'),
        ('invited', 'Invited'),
        ('rejected', 'Rejected'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    def is_expired(self):
        return timezone.now() > self.expires_at

    @property
    def is_verified(self):
        return self.verified_at is not None

    def __str__(self):
        return f"Application from {self.email} to {self.community.name}"


class ReferenceRequest(models.Model):
    application = models.ForeignKey(MembershipApplication, on_delete=models.CASCADE, related_name='reference_requests')
    referrer = models.ForeignKey(CommunityMember, on_delete=models.CASCADE, related_name='sent_references')
    message = models.TextField(blank=True)
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('declined', 'Declined'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_accepted(self):
        return self.status == 'accepted'

    def __str__(self):
        return f"Reference by {self.referrer.display_name} for {self.application.email}"
