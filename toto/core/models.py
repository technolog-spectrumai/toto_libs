from django_jsonform.models.fields import JSONField
from django.core.exceptions import ValidationError
from django.db import models
from colorfield.fields import ColorField
from django.contrib.auth import get_user_model
from django.utils.text import slugify
from toto.core.domain import DomainEntity

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


_API_AUTH_CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "header_name": {"type": "string", "default": "Authorization"},
        "header_prefix": {"type": "string", "default": "Bearer"},
        "query_param_name": {"type": "string"},
        "timeout_seconds": {
            "type": "integer",
            "minimum": 1,
            "maximum": 120,
            "default": 30,
        },
    },
    "additionalProperties": False,
}

_FORBIDDEN_SECRET_KEYS = {
    "api_key", "apikey", "token", "secret",
    "password", "private_key", "client_secret",
}


def _reject_secret_like_json(value):
    def walk(obj):
        if isinstance(obj, dict):
            for key, nested in obj.items():
                if str(key).lower().replace("-", "_") in _FORBIDDEN_SECRET_KEYS:
                    raise ValidationError(
                        f'Do not store secret-like value "{key}" in connector config. '
                        "Use a Gervazy EncryptedSecret instead."
                    )
                walk(nested)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)
    walk(value)


class ApiConnector(models.Model):
    """Generic outbound API connector. Configuration only — secrets live in Gervazy."""

    AUTH_NONE = "none"
    AUTH_API_KEY_HEADER = "api_key_header"
    AUTH_BEARER_TOKEN = "bearer_token"
    AUTH_QUERY_PARAM = "query_param"
    AUTH_HMAC = "hmac"
    AUTH_SIGNATURE = "signature"
    AUTH_CUSTOM = "custom"

    AUTH_TYPE_CHOICES = [
        (AUTH_NONE, "No authentication"),
        (AUTH_API_KEY_HEADER, "API key header"),
        (AUTH_BEARER_TOKEN, "Bearer token"),
        (AUTH_QUERY_PARAM, "Query parameter"),
        (AUTH_HMAC, "HMAC signature"),
        (AUTH_SIGNATURE, "Asymmetric request signature"),
        (AUTH_CUSTOM, "Custom"),
    ]

    PROVIDER_GENERIC = "generic"
    PROVIDER_OPENAI = "openai"
    PROVIDER_ANTHROPIC = "anthropic"
    PROVIDER_GITHUB = "github"
    PROVIDER_CUSTOM = "custom"
    PROVIDER_RULE_BASED = "rule_based"

    PROVIDER_CHOICES = [
        (PROVIDER_GENERIC, "Generic"),
        (PROVIDER_OPENAI, "OpenAI"),
        (PROVIDER_ANTHROPIC, "Anthropic"),
        (PROVIDER_GITHUB, "GitHub"),
        (PROVIDER_CUSTOM, "Custom"),
        (PROVIDER_RULE_BASED, "Rule-based (no API key)"),
    ]

    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    provider = models.CharField(
        max_length=40, choices=PROVIDER_CHOICES, default=PROVIDER_GENERIC
    )
    base_url = models.URLField(
        blank=True, help_text="Base API URL, e.g. https://api.openai.com/v1/"
    )
    auth_type = models.CharField(
        max_length=40, choices=AUTH_TYPE_CHOICES, default=AUTH_BEARER_TOKEN
    )
    api_secret = models.ForeignKey(
        "gervazy.EncryptedSecret",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="%(class)s_api_connectors",
        help_text="Encrypted API key/token stored in Gervazy.",
    )
    signing_key = models.ForeignKey(
        "gervazy.EncryptedPrivateKey",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="%(class)s_signing_keys",
        help_text="Encrypted private key for signed API requests.",
    )
    owner = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="%(class)s_owned",
    )
    auth_config = JSONField(
        schema=_API_AUTH_CONFIG_SCHEMA,
        default=dict,
        blank=True,
        help_text="Non-secret auth config such as header name or timeout.",
    )
    extra = JSONField(
        default=dict,
        blank=True,
        validators=[_reject_secret_like_json],
        help_text="Non-secret provider-specific configuration.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name) or "api-connector"
            slug = base_slug
            counter = 1
            while type(self).objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def get_timeout(self):
        return int(self.auth_config.get("timeout_seconds") or 30)

    def build_auth_headers(self, *, vault_session):
        """Return HTTP auth headers. vault_session must be an unlocked Gervazy vault session."""
        if not self.is_active:
            raise RuntimeError(f'API connector "{self.name}" is inactive.')
        if self.auth_type == self.AUTH_NONE:
            return {}
        if self.auth_type == self.AUTH_BEARER_TOKEN:
            token = vault_session.decrypt_secret(self.api_secret)
            return {"Authorization": f"Bearer {token}"}
        if self.auth_type == self.AUTH_API_KEY_HEADER:
            header_name = self.auth_config.get("header_name") or "Authorization"
            prefix = self.auth_config.get("header_prefix", "")
            api_key = vault_session.decrypt_secret(self.api_secret)
            value = f"{prefix} {api_key}".strip() if prefix else api_key
            return {header_name: value}
        raise RuntimeError(
            f"Auth type {self.auth_type} requires custom request handling."
        )


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
    signing_secret = models.ForeignKey(
        "gervazy.EncryptedSecret",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_signing_secrets",
        help_text="Encrypted secret used for platform token signing.",
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
    api_signing_key_out = models.ForeignKey(
        "gervazy.EncryptedPrivateKey",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_outbound_signing",
        help_text="Encrypted private key used when this platform sends signed sync requests.",
    )
    api_verify_key_in = models.TextField(
        null=True,
        blank=True,
        help_text="Public key PEM used to verify incoming sync requests.",
    )

    def __str__(self):
        return f"{self.site_name} Platform"
