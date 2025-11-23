from django_jsonform.models.fields import JSONField
from django.db import models
from django.utils import timezone
from django.core.management import call_command
from django.apps import apps
import os
from io import StringIO
import sys
import json
from django.conf import settings
from django.db import models
from colorfield.fields import ColorField  # Ensure you have django-colorfield installed
from gervazy.models import SecretKey

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
        return {
            "colors": {
                # Light mode
                "primary-bg-light": self.color_mix.primary_bg_light,
                "header-bg-light": self.color_mix.header_bg_light,
                "appbar-bg-light": self.color_mix.appbar_bg_light,
                "appbar-text-light": self.color_mix.appbar_text_light,
                "bubble-bg-light": self.color_mix.bubble_bg_light,
                "footer-bg-light": self.color_mix.footer_bg_light,
                "footer-text-light": self.color_mix.footer_text_light,
                "text-main-light": self.color_mix.text_main_light,
                "accent-light": self.color_mix.accent_light,
                "warn-light": self.color_mix.warn_light,

                # Dark mode
                "primary-bg-dark": self.color_mix.primary_bg_dark,
                "header-bg-dark": self.color_mix.header_bg_dark,
                "appbar-bg-dark": self.color_mix.appbar_bg_dark,
                "appbar-text-dark": self.color_mix.appbar_text_dark,
                "bubble-bg-dark": self.color_mix.bubble_bg_dark,
                "footer-bg-dark": self.color_mix.footer_bg_dark,
                "footer-text-dark": self.color_mix.footer_text_dark,
                "text-main-dark": self.color_mix.text_main_dark,
                "accent-dark": self.color_mix.accent_dark,
                "warn-dark": self.color_mix.warn_dark,

                # Accent colors
                "accent-1": self.color_mix.accent_1,
                "accent-2": self.color_mix.accent_2
            }
        }


class Platform(models.Model):
    domain = models.CharField(max_length=255, null=True, blank=True)
    site_name = models.CharField(max_length=255)

    publication_year = models.IntegerField()
    active = models.BooleanField(default=True)

    theme = models.ForeignKey(
        Theme,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_themes",
        help_text="Theme applied to this platform"
    )
    secret = models.OneToOneField(
        SecretKey,
        on_delete=models.CASCADE,
        related_name="platform",
        help_text="SecretKey used for signing tokens"
    )
    rate_limit_window = models.IntegerField(
        default=60,
        help_text="Rate limit window in seconds"
    )
    rate_limit_max_requests = models.IntegerField(
        default=20,
        help_text="Max requests allowed per window"
    )

    def __str__(self):
        return f"{self.site_name} Platform"


class DashboardBlock(models.Model):
    title = models.CharField(max_length=100)
    description = models.TextField()
    icon = models.CharField(max_length=50)
    link = models.CharField(blank=True, null=True, max_length=64)
    public = models.BooleanField(default=True)

    def __str__(self):
        return self.title


_INGRESS_ALLOWED_APPS = getattr(settings, "INGRESS_ALLOWED_APPS", [])


class AppIngress(models.Model):

    class IngressCommandError(Exception):
        """Base class for ingress command errors."""

    class IngressCommandNotFound(IngressCommandError):
        """Raised when the ingress command file is missing."""

    class IngressCommandExecutionFailed(IngressCommandError):
        """Raised when the command execution throws an error."""

    INGRESS_ALLOWED_APPS = _INGRESS_ALLOWED_APPS
    app_name = models.CharField(
        max_length=64,
        choices=[(app, app) for app in _INGRESS_ALLOWED_APPS],
        help_text="Target app for ingress"
    )
    scheduled_at = models.DateTimeField(
        default=timezone.now,
        help_text="When this ingress should be executed"
    )

    def __str__(self):
        return f"Ingress for {self.app_name} at {self.scheduled_at}"

    def run_ingress_command(self):
        """
        Runs the 'ingress' management command for the specified app,
        passing args as a JSON string.
        Raises:
            IngressCommandNotFound: if the command file doesn't exist
            IngressCommandExecutionFailed: if execution fails
        """
        app_config = apps.get_app_config(self.app_name)
        cmd_path = os.path.join(app_config.path, "management", "commands", f"ingress_{self.app_name}.py")

        if not os.path.isfile(cmd_path):
            raise self.IngressCommandNotFound(f"No ingress command found for '{self.app_name}'")

        try:
            out = StringIO()
            full_ingress_mode = getattr(settings, 'FULL_INGRESS', False)
            call_command(f"ingress_{self.app_name}", stdout=out, stderr=out, full=full_ingress_mode)
            output = out.getvalue()
            sys.stdout.write(output)

        except Exception as e:
            raise self.IngressCommandExecutionFailed(f"Error running ingress for '{self.app_name}': {str(e)}")


