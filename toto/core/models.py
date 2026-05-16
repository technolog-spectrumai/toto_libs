from django.db import models
from colorfield.fields import ColorField
from django.contrib.auth import get_user_model
from django_jsonform.models.fields import JSONField
from toto.core.domain import DomainEntity

# Moved to toto.api — kept for backward compatibility only.
from toto.api.models import ApiConnector as ApiConnector  # noqa: F401
from toto.api.models import _reject_secret_like_json as _reject_secret_like_json  # noqa: F401 (existing migrations reference this dotted path)

User = get_user_model()


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

_THEME_EXTRA = {
    "type": "object",
    "properties": {
        "light": {"type": "string"},
        "dark": {"type": "string"}
    },
    "required": ["light", "dark"],
    "additionalProperties": False
}


class ColorMix(models.Model):
    name = models.CharField(
        max_length=64,
        unique=True,
        help_text="Name of the color mix"
    )

    # Light mode colors
    primary_bg_light = ColorField(default="#FFFFFF")
    header_bg_light = ColorField(default="#FFFFFF")
    appbar_bg_light = ColorField(default="#FFFFFF")
    appbar_text_light = ColorField(default="#000000")
    footer_bg_light = ColorField(default="#FFFFFF")
    footer_text_light = ColorField(default="#000000")
    bubble_bg_light = ColorField(default="#FFFFFF")
    text_main_light = ColorField(default="#000000")
    accent_light = ColorField(default="#FF4081")
    warn_light = ColorField(default="#FFC107")

    # NEW
    success_light = ColorField(default="#4CAF50")   # green
    sunken_light = ColorField(default="#F5F5F5")    # subtle grey
    link_light = ColorField(default="#1E88E5")      # blue

    # Dark mode colors
    primary_bg_dark = ColorField(default="#121212")
    header_bg_dark = ColorField(default="#1F1F1F")
    appbar_bg_dark = ColorField(default="#1F1F1F")
    appbar_text_dark = ColorField(default="#FFFFFF")
    footer_bg_dark = ColorField(default="#1F1F1F")
    footer_text_dark = ColorField(default="#FFFFFF")
    bubble_bg_dark = ColorField(default="#2C2C2C")
    text_main_dark = ColorField(default="#FFFFFF")
    accent_dark = ColorField(default="#FF4081")
    warn_dark = ColorField(default="#FF5722")

    # NEW
    success_dark = ColorField(default="#66BB6A")    # lighter green for dark mode
    sunken_dark = ColorField(default="#1A1A1A")     # deeper grey
    link_dark = ColorField(default="#64B5F6")       # lighter blue

    # Accent colors
    accent_1 = ColorField(default="#03A9F4")
    accent_2 = ColorField(default="#4CAF50")

    def __str__(self):
        return self.name



class Theme(models.Model):

    name = models.CharField(
        max_length=64,
        unique=True,
        help_text="Name of the theme"
    )

    color_mix = models.ForeignKey(
        ColorMix,
        on_delete=models.CASCADE,
        related_name="themes",
        help_text="Color palette used for this theme"
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
        schema=_THEME_EXTRA,
        default=dict,
        help_text="Tailwind classes for header element (light/dark mode)"
    )

    footer = JSONField(
        schema=_THEME_EXTRA,
        default=dict,
        help_text="Tailwind classes for footer element (light/dark mode)"
    )

    def __str__(self):
        return self.name

    @property
    def theme(self):
        colors = {}

        # Loop through all fields in ColorMix
        for field in self.color_mix._meta.get_fields():
            if hasattr(self.color_mix, field.name):
                value = getattr(self.color_mix, field.name)

                # Only include ColorField values (hex colors)
                if isinstance(value, str) and value.startswith("#"):
                    colors[field.name.replace("_", "-")] = value

        return {"colors": colors}


class Federation(DomainEntity):
    name = models.CharField(max_length=100, unique=True)
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


class Platform(models.Model):
    domain = models.CharField(max_length=255, null=True, blank=True)
    site_name = models.CharField(max_length=255)
    author = models.CharField(max_length=255)
    publication_year = models.IntegerField()
    active = models.BooleanField(default=True)

    theme = models.ForeignKey(
        Theme,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_themes",
        help_text="Theme applied to this platform",
    )
    rate_limit_window = models.IntegerField(
        default=60,
        help_text="Rate limit window in seconds",
    )
    rate_limit_max_requests = models.IntegerField(
        default=20,
        help_text="Max requests allowed per window",
    )
    logo = models.ImageField(
        upload_to="federation_logos/",
        null=True,
        blank=True,
        help_text="Optional logo for this platform",
    )
    federation = models.ForeignKey(
        Federation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_federations",
        help_text="Federation this platform belongs to",
    )
    api_url = models.URLField(
        null=True,
        blank=True,
        help_text="Base API endpoint for this platform (e.g. https://example.com/api/)",
    )
    api_owner = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_api_accounts",
        help_text="User account used for API authentication",
    )
    def __str__(self):
        return f"{self.site_name} Platform"
