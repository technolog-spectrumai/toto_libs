from django.contrib.auth.models import User
from django.db import models
from gervazy.models import TLSCertificate
from django.db import models
from django.contrib.auth.models import User
from django_jsonform.models.fields import JSONField
from django.utils import timezone
import random



class Address(models.Model):
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
        full = f"{base}, {self.locality_name}, {self.state_or_province_name}, {self.country_name}"
        return full


class Font(models.Model):
    FONT_FAMILY_CHOICES = [
        ("serif", "Serif"),
        ("sans-serif", "Sans-serif"),
        ("monospace", "Monospace"),
        ("display", "Display / Decorative"),
    ]

    name = models.CharField(
        max_length=64,
        unique=True,
        help_text="Display name of the font (e.g. 'Playfair Display')"
    )

    cdn_link = models.URLField(
        help_text="CDN or stylesheet URL for importing the font (e.g. Google Fonts)"
    )

    style_family = models.CharField(
        max_length=20,
        choices=FONT_FAMILY_CHOICES,
        default="sans-serif",
        help_text="Font style family classification"
    )

    def __str__(self):
        return f"{self.name} ({self.style_family})"

_SCHEMA = {
    "type": "object",
    "properties": {
        "colors": {
            "type": "object",
            "properties": {
                "primary-bg-light": {"type": "string", "pattern": "^#([A-Fa-f0-9]{6})$"},
                "header-bg-light": {"type": "string", "pattern": "^#([A-Fa-f0-9]{6})$"},
                "appbar-bg-light": {"type": "string", "pattern": "^#([A-Fa-f0-9]{6})$"},
                "bubble-bg-light": {"type": "string", "pattern": "^#([A-Fa-f0-9]{6})$"},
                "text-main-light": {"type": "string", "pattern": "^#([A-Fa-f0-9]{6})$"},
                "primary-bg-dark": {"type": "string", "pattern": "^#([A-Fa-f0-9]{6})$"},
                "header-bg-dark": {"type": "string", "pattern": "^#([A-Fa-f0-9]{6})$"},
                "appbar-bg-dark": {"type": "string", "pattern": "^#([A-Fa-f0-9]{6})$"},
                "bubble-bg-dark": {"type": "string", "pattern": "^#([A-Fa-f0-9]{6})$"},
                "text-main-dark": {"type": "string", "pattern": "^#([A-Fa-f0-9]{6})$"},
                "accent-light": {"type": "string", "pattern": "^#([A-Fa-f0-9]{6})$"},
                "accent-dark": {"type": "string", "pattern": "^#([A-Fa-f0-9]{6})$"},
                "warn-light": {"type": "string", "pattern": "^#([A-Fa-f0-9]{6})$"},
                "warn-dark": {"type": "string", "pattern": "^#([A-Fa-f0-9]{6})$"},
                "accent-1": {"type": "string", "pattern": "^#([A-Fa-f0-9]{6})$"},
                "accent-2": {"type": "string", "pattern": "^#([A-Fa-f0-9]{6})$"}
            },
            "required": [
                "primary-bg-light", "header-bg-light", "appbar-bg-light", "bubble-bg-light", "text-main-light",
                "primary-bg-dark", "header-bg-dark", "appbar-bg-dark", "bubble-bg-dark", "text-main-dark",
                "accent-light", "accent-dark", "warn-light", "warn-dark",
                "accent-1", "accent-2"
            ],
            "additionalProperties": False
        }
    },
    "required": ["colors"],
    "additionalProperties": False
}

_HEADER = {
    "type": "object",
    "title": "Header Style Configuration",
    "properties": {
        "border": {
            "type": "string",
            "title": "Border Style",
            "enum": [
                "border", "border-0", "border-2", "border-4", "border-8",
                "border-t", "border-r", "border-b", "border-l",
                "border-x", "border-y",
                "border-t-2", "border-r-2", "border-b-2", "border-l-2", "border-x-2", "border-y-2",
                "border-t-4", "border-b-4", "border-l-4"
            ],
            "description": "Tailwind border utility classes for thickness and edge control"
        },
        "color_light": {
            "type": "string",
            "title": "Border Color (Light Mode)",
            "description": "e.g. border-accent-2"
        },
        "color_dark": {
            "type": "string",
            "title": "Border Color (Dark Mode)",
            "description": "e.g. border-white"
        },
        "radius": {
            "type": "string",
            "title": "Corner Radius",
            "enum": [
                "rounded-none", "rounded-sm", "rounded", "rounded-md",
                "rounded-lg", "rounded-xl", "rounded-2xl", "rounded-3xl", "rounded-full"
            ]
        },
        "shadow": {
            "type": "string",
            "title": "Shadow Style",
            "enum": [
                "shadow-sm", "shadow", "shadow-md", "shadow-lg",
                "shadow-xl", "shadow-2xl", "shadow-inner", "shadow-none"
            ]
        }
    },
    "required": ["border"]
}




class Theme(models.Model):

    SCHEMA = _SCHEMA
    name = models.CharField(
        max_length=64,
        unique=True,
        help_text="Name of the theme"
    )

    theme = JSONField(
        schema=_SCHEMA,
        default={},
        help_text="Tailwind classes for page-level layout elements"
    )
    font = models.ForeignKey(
        Font,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="themes",
        help_text="Font applied to this theme"
    )

    header = JSONField(
        schema=_HEADER,
        default={},
        help_text="Tailwind utilities for header border, radius, and shadow appearance"
    )

    def __str__(self):
        return self.name

    def get_header_classes(self, dark_mode=True):
        style = self.header or {}
        border = style.get("border", "")
        color_light = style.get("color_light", "")
        color_dark = style.get("color_dark", "")
        radius = style.get("radius", "")
        shadow = style.get("shadow", "")

        classes = [
            border,
            color_dark if dark_mode else color_light,
            radius if radius.startswith("rounded") else f"rounded-{radius}" if radius else "",
            shadow
        ]

        return " ".join(filter(None, classes))

class Company(models.Model):
    name = models.CharField(max_length=255, help_text="Legal or brand name of the company")
    address = models.ForeignKey(Address, on_delete=models.SET_NULL, null=True, blank=True)
    established_year = models.IntegerField(null=True, blank=True, help_text="Year the company was founded")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Platform(models.Model):
    domain = models.CharField(max_length=255, null=True, blank=True)
    site_name = models.CharField(max_length=255)

    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platforms",
        help_text="Company that owns this platform (optional)"
    )

    publication_year = models.IntegerField()
    active = models.BooleanField(default=True)
    index_url = models.CharField(max_length=255, blank=True, null=True)

    tls_certificate = models.OneToOneField(
        TLSCertificate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="TLS Certificate"
    )
    theme = models.ForeignKey(
        Theme,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_themes",
        help_text="Theme applied to this platform"
    )

    def __str__(self):
        return f"{self.site_name} ({self.company if self.company else ''})"


class Branch(models.Model):
    platform = models.ForeignKey(
        Platform,
        on_delete=models.CASCADE,
        related_name="branches",
        help_text="Main company this branch belongs to"
    )
    name = models.CharField(
        max_length=255,
        help_text="Name of the branch"
    )
    address = models.ForeignKey(
        Address,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Branch address"
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} - {str(self.platform)}"

    class Meta:
        verbose_name_plural = "Branches"


class DashboardBlock(models.Model):
    title = models.CharField(max_length=100)
    description = models.TextField()
    icon = models.CharField(max_length=50)
    link = models.URLField(blank=True, null=True)

    def __str__(self):
        return self.title


class CommunityMember(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='community_profile',
        help_text='Linked Django user account'
    )
    membership = models.ManyToManyField(
        Branch,
        related_name='members',
        help_text='Branches this member belongs to'
    )
    display_name = models.CharField(max_length=150)
    bio = models.TextField(null=True, blank=True)
    avatar = models.ImageField(upload_to='avatars/', null=True, blank=True)
    joined_date = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.display_name


def generate_code(k=6):
    return ''.join(random.choices('0123456789', k=k))


class MembershipApplication(models.Model):
    email = models.EmailField(unique=True)
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name='applications',
        help_text="Branch this application is targeting"
    )
    code = models.CharField(
        max_length=10,
        unique=True,
        default=generate_code,
        help_text="Unique application code"
    )
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
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        help_text="Current status of the application"
    )

    def is_expired(self):
        return timezone.now() > self.expires_at

    @property
    def is_verified(self):
        return self.verified_at is not None

    def __str__(self):
        return f"Application from {self.email} to {self.branch.name}"


class ReferenceRequest(models.Model):
    application = models.ForeignKey(
        MembershipApplication,
        on_delete=models.CASCADE,
        related_name='reference_requests',
        help_text="Membership application this reference request is linked to"
    )
    referrer = models.ForeignKey(
        CommunityMember,
        on_delete=models.CASCADE,
        related_name='sent_references',
        help_text="Community member who is endorsing the applicant"
    )
    message = models.TextField(
        blank=True,
        help_text="Optional message from the referrer about the applicant"
    )
    status_choices = [
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('declined', 'Declined'),
    ]
    status = models.CharField(
        max_length=20,
        choices=status_choices,
        default='pending',
        help_text="Status of the referral"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_accepted(self):
        return self.status == 'accepted'

    def __str__(self):
        return f"Reference by {self.referrer.display_name} for {self.application.email}"



