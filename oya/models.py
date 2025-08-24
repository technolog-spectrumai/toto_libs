from django_jsonform.models.fields import JSONField
from django.db import models
from django.utils import timezone
from django.core.management import call_command
from django.apps import apps
import os
from io import StringIO

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
    "properties": {
        "light": {"type": "string"},
        "dark": {"type": "string"}
    },
    "required": ["light", "dark"],
    "additionalProperties": False
}



class Theme(models.Model):

    SCHEMA = _SCHEMA
    HEADER = _HEADER
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
        default=dict,
        help_text="Tailwind classes for header element (light/dark mode)"
    )

    def __str__(self):
        return self.name


class Platform(models.Model):
    domain = models.CharField(max_length=255, null=True, blank=True)
    site_name = models.CharField(max_length=255)

    publication_year = models.IntegerField()
    active = models.BooleanField(default=True)
    index_url = models.CharField(max_length=255, blank=True, null=True)

    theme = models.ForeignKey(
        Theme,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_themes",
        help_text="Theme applied to this platform"
    )

    def __str__(self):
        return f"{self.site_name} Platform"


class DashboardBlock(models.Model):
    title = models.CharField(max_length=100)
    description = models.TextField()
    icon = models.CharField(max_length=50)
    link = models.URLField(blank=True, null=True)

    def __str__(self):
        return self.title


ALLOWED_APPS = [
    'oya',
    'webfront',
    "nakamori",
    "memo",
    "resume",
    "documents",
    "kodama"
]

class AppIngress(models.Model):
    app_name = models.CharField(
        max_length=64,
        choices=[(app, app) for app in ALLOWED_APPS],
        help_text="Target app for ingress"
    )
    args = models.JSONField(
        default=dict,
        blank=True,
        help_text="Arguments to pass to the ingress command"
    )
    scheduled_at = models.DateTimeField(
        default=timezone.now,
        help_text="When this ingress should be executed"
    )

    def __str__(self):
        return f"Ingress for {self.app_name} at {self.scheduled_at}"

    def run_ingress_command(self):
        """
        Runs the 'ingress' management command for the specified app.
        """
        try:
            app_config = apps.get_app_config(self.app_name)
            cmd_path = os.path.join(app_config.path, "management", "commands", f"ingress_{self.app_name}.py")
            if os.path.isfile(cmd_path):
                out = StringIO()
                call_command(f"ingress_{self.app_name}", **self.args, stdout=out, stderr=out)
                output = out.getvalue()
                return f"Success: Ran ingress for {self.app_name}", 0
            else:
                return f"No ingress command found for {self.app_name}", -1
        except Exception as e:
            return f"Error running ingress for {self.app_name}: {str(e)}", 1


