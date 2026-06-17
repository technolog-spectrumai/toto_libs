import random
import uuid

from django.contrib.auth.models import User
from django.db import models
from django.urls import reverse
from django.utils.html import strip_tags
from django.utils import timezone
from django.utils.text import Truncator
from django.utils.text import slugify

from toto.core.domain import DomainEntity
from toto.core.models import Federation
from toto.locations.models import Address, Territory
from toto.people.models import Person  # re-exported for backward compat  # noqa: F401
from toto.api.models import EmailService as EmailService  # re-exported for backward compat  # noqa: F401
from toto.verbena.models import AbstractSection, AbstractTag
from toto.verbena.utils import unique_slug


class Community(DomainEntity):
    GUILD = "guild"
    COMPANY = "company"
    NON_PROFIT = "non_profit"
    FAMILY = "family"
    OTHER = "other"

    ORG_TYPES = [
        (GUILD, "Guild"),
        (COMPANY, "Company"),
        (NON_PROFIT, "Non-Profit"),
        (FAMILY, "Family"),
        (OTHER, "Other"),
    ]

    name = models.CharField(max_length=255, help_text="Name of the community or organization")
    slug = models.SlugField(unique=True, blank=True, help_text="URL-friendly identifier")

    org_type = models.CharField(
        max_length=20,
        choices=ORG_TYPES,
        default=OTHER,
        help_text="Type of organization"
    )

    location = models.ForeignKey(Address, on_delete=models.SET_NULL, null=True, blank=True, related_name="community_locations")
    territory = models.ForeignKey(Territory, on_delete=models.SET_NULL, null=True, blank=True, related_name="community_territories")
    established_year = models.IntegerField(null=True, blank=True)

    head = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="headed_communities",
    )
    senior_members = models.ManyToManyField(
        "people.Person",
        related_name="senior_communities",
        blank=True,
        help_text="Members allowed to manage community announcements and news.",
    )

    logo = models.ImageField(
        upload_to='community_logos/',
        null=True,
        blank=True,
        help_text="Optional logo for this federation"
    )

    is_autonomous = models.BooleanField(
        default=False,
        help_text="Marks this community as self-governing, with its own internal leadership and rules."
    )
    updated_at = models.DateTimeField(auto_now=True)
    email = models.EmailField(unique=True, blank=True, null=True)
    is_foreign = models.BooleanField(default=False,
                                     help_text="Indicates whether this federation originates outside the local jurisdiction")
    is_federal_tribe = models.BooleanField(
        default=False,
        help_text="Members of this community are exempt from all poll taxes (federal tax exemption).",
    )
    email_service = models.ForeignKey(
        "api.EmailService",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='community_emails',
    )

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

    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children",
        help_text="Parent community in the hierarchy",
    )

    federation = models.ForeignKey(
        Federation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="communities"
    )


class CommunityNewsTopic(AbstractTag):
    class Meta:
        verbose_name = "Community news topic"
        verbose_name_plural = "Community news topics"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(self, self.name, fallback="topic")
        super().save(*args, **kwargs)


class CommunityNewsPost(AbstractSection):
    PUBLIC = "public"
    COMMUNITY = "community"

    VISIBILITY_CHOICES = [
        (PUBLIC, "Public"),
        (COMMUNITY, "Community"),
    ]

    author = models.ForeignKey(
        "people.Person",
        related_name="community_news_posts",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    community = models.ForeignKey(
        Community,
        related_name="news_posts",
        on_delete=models.CASCADE,
    )
    topics = models.ManyToManyField(
        CommunityNewsTopic,
        related_name="posts",
        blank=True,
    )
    visibility = models.CharField(max_length=20, choices=VISIBILITY_CHOICES, default=PUBLIC)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Community news post"
        verbose_name_plural = "Community news posts"

    @property
    def plain_text(self):
        return " ".join(strip_tags(self.content or "").split())

    @property
    def excerpt(self):
        return Truncator(self.plain_text).chars(220)

    @property
    def display_title(self):
        return self.title or self.excerpt or "Untitled post"

    def get_absolute_url(self):
        return f"{reverse('socialhub:community_detail', args=[self.community.slug])}#community-news-post-{self.pk}"

    def __str__(self):
        return self.display_title



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
    referrer = models.ForeignKey("people.Person", on_delete=models.CASCADE, related_name="sent_references")
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

    def save(self, *args, **kwargs):
        status_changed_to_accepted = False

        if self.pk:
            old = ReferenceRequest.objects.get(pk=self.pk)
            if old.status != "accepted" and self.status == "accepted":
                status_changed_to_accepted = True

        super().save(*args, **kwargs)

        if status_changed_to_accepted:
            application = self.application

            # 1. Activate the user — looked up by their application email (the login
            #    username is chosen separately, so we must not match on username here).
            user = User.objects.filter(email=application.email).first()
            if user:
                user.is_active = True
                user.save(update_fields=["is_active"])

            # 2. Create Person if missing
            if user:
                member, created = Person.objects.get_or_create(
                    user=user,
                    defaults={
                        "display_name": user.username,
                        "email": user.email,
                    }
                )

                # 3. Add them to the community they applied to
                member.communities.add(application.community)
                member.save()


def _constitution_slug(instance, value):
    base = slugify(value) or "constitution"
    slug = base
    n = 1
    qs = Constitution.objects.exclude(pk=instance.pk)
    while qs.filter(slug=slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


class Constitution(models.Model):
    community = models.ForeignKey(
        Community,
        on_delete=models.CASCADE,
        related_name="constitutions",
    )
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    body = models.TextField()
    version = models.CharField(max_length=50, blank=True, help_text="e.g. I, II, 2025-rev1")
    is_active = models.BooleanField(
        default=True,
        help_text="Only the active constitution is shown on the community page.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Constitution"
        verbose_name_plural = "Constitutions"

    def __str__(self):
        v = f" ({self.version})" if self.version else ""
        return f"{self.community.name} — {self.title}{v}"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _constitution_slug(self, self.title)
        super().save(*args, **kwargs)

    @property
    def signature_count(self):
        return self.signatures.filter(signed_at__isnull=False).count()


class ConstitutionSignature(models.Model):
    constitution = models.ForeignKey(
        Constitution,
        on_delete=models.CASCADE,
        related_name="signatures",
    )
    person = models.ForeignKey(
        "people.Person",
        on_delete=models.CASCADE,
        related_name="constitution_signatures",
    )
    signed_at = models.DateTimeField(null=True, blank=True)

    signature_data = models.TextField(
        blank=True,
        help_text="Base64-encoded PNG — decorative handwritten signature image.",
    )
    signing_payload = models.TextField(
        blank=True,
        help_text="Canonical UTF-8 payload that was signed.",
    )
    cryptographic_signature = models.TextField(
        blank=True,
        help_text="Base64-encoded Ed25519 signature over signing_payload.",
    )
    signing_key = models.ForeignKey(
        "gervazy.EncryptedPrivateKey",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="constitution_signatures",
        help_text="The EncryptedPrivateKey used to produce the cryptographic signature.",
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("constitution", "person")]
        ordering = ["added_at"]
        verbose_name = "Constitution signature"
        verbose_name_plural = "Constitution signatures"

    def __str__(self):
        signed = "signed" if self.signed_at else "pending"
        return f"{self.person} on {self.constitution.title} [{signed}]"

    @property
    def has_signed(self):
        return self.signed_at is not None

    @property
    def is_cryptographically_signed(self):
        return bool(self.cryptographic_signature and self.signing_payload)
