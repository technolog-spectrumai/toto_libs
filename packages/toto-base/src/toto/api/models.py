import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage, get_connection
from django.db import models
from django.utils.text import slugify
from django_jsonform.models.fields import JSONField

User = get_user_model()


# ---------------------------------------------------------------------------
# ApiConnector (abstract base for outbound API integrations)
# ---------------------------------------------------------------------------

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
        settings.AUTH_USER_MODEL,
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

    def decrypt_api_secret(self, *, vault_session):
        if vault_session is None:
            raise RuntimeError(
                f'API connector "{self.name}" requires a Gervazy vault session to resolve credentials.'
            )
        if self.api_secret_id is None:
            raise RuntimeError(f'API connector "{self.name}" has no api_secret configured.')
        return vault_session.decrypt_secret(self.api_secret)

    def build_auth_headers(self, *, vault_session):
        """Return HTTP auth headers. vault_session must be an unlocked Gervazy vault session."""
        if not self.is_active:
            raise RuntimeError(f'API connector "{self.name}" is inactive.')
        if self.auth_type == self.AUTH_NONE:
            return {}
        if self.auth_type == self.AUTH_QUERY_PARAM:
            return {}
        if self.auth_type == self.AUTH_BEARER_TOKEN:
            token = self.decrypt_api_secret(vault_session=vault_session)
            return {"Authorization": f"Bearer {token}"}
        if self.auth_type == self.AUTH_API_KEY_HEADER:
            header_name = self.auth_config.get("header_name") or "Authorization"
            prefix = self.auth_config.get("header_prefix", "")
            api_key = self.decrypt_api_secret(vault_session=vault_session)
            value = f"{prefix} {api_key}".strip() if prefix else api_key
            return {header_name: value}
        raise RuntimeError(
            f"Auth type {self.auth_type} requires custom request handling."
        )

    def build_auth_query_params(self, *, vault_session):
        """Return HTTP auth query params for connector types that use them."""
        if not self.is_active:
            raise RuntimeError(f'API connector "{self.name}" is inactive.')
        if self.auth_type == self.AUTH_NONE:
            return {}
        if self.auth_type == self.AUTH_QUERY_PARAM:
            param_name = self.auth_config.get("query_param_name") or "api_key"
            return {param_name: self.decrypt_api_secret(vault_session=vault_session)}
        if self.auth_type in (self.AUTH_BEARER_TOKEN, self.AUTH_API_KEY_HEADER):
            return {}
        raise RuntimeError(
            f"Auth type {self.auth_type} requires custom request handling."
        )


class Connector(ApiConnector):
    """Concrete generic API connector for non-specialized outbound integrations."""

    class Meta(ApiConnector.Meta):
        verbose_name = "API connector"
        verbose_name_plural = "API connectors"


# ---------------------------------------------------------------------------
# EmailService (SMTP configuration, secrets live in Gervazy)
# ---------------------------------------------------------------------------

class EmailService(models.Model):
    """
    Stores SMTP configuration for a system component.
    The SMTP password is stored in a Gervazy EncryptedSecret.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(
        max_length=128,
        unique=True,
        blank=True,
        help_text="Optional name for this email service. Auto-generated if omitted."
    )
    email_address = models.EmailField(help_text="SMTP login email address")

    smtp_secret = models.ForeignKey(
        "gervazy.EncryptedSecret",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="email_services",
        help_text="Gervazy EncryptedSecret holding the SMTP password.",
    )

    host = models.CharField(max_length=255, help_text="SMTP server hostname")
    port = models.PositiveIntegerField(default=587)
    use_tls = models.BooleanField(default=True)
    use_ssl = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "socialhub_emailservice"

    def save(self, *args, **kwargs):
        if not self.name:
            self.name = f"email-service-{uuid.uuid4()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"EmailService {self.name} ({self.email_address})"

    def send_email(self, subject, body, to, html=None, *, smtp_password: str):
        """
        Sends an email using this EmailService's SMTP configuration.
        Pass smtp_password explicitly — obtain it from the Gervazy vault session.
        """
        connection = get_connection(
            backend=settings.EMAIL_BACKEND,
            host=self.host,
            port=self.port,
            username=self.email_address,
            password=smtp_password,
            use_tls=self.use_tls,
            use_ssl=self.use_ssl,
        )

        msg = EmailMessage(
            subject=subject,
            body=body,
            from_email=self.email_address,
            to=[to] if isinstance(to, str) else to,
            connection=connection,
        )

        if html:
            msg.content_subtype = "html"
            msg.body = html

        return msg.send()
