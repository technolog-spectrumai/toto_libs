from django.conf import settings
from django.db import models
from django.utils.text import slugify

from toto.api.models import ApiConnector
from toto.sabbia.endpoints import ENDPOINT_CHOICES


class AgentConnector(ApiConnector):
    """Concrete ApiConnector subclass so agent API keys live encrypted in Gervazy.

    Inherits name/slug/provider/base_url/auth_type/api_secret/owner/extra and the
    ``decrypt_api_secret(vault_session=...)`` helper from ``toto.api.ApiConnector``.
    """

    class Meta(ApiConnector.Meta):
        verbose_name = "Agent connector"
        verbose_name_plural = "Agent connectors"


class Agent(models.Model):
    """A chat-only agent. Reaches its model through one HTTP endpoint."""

    ENDPOINT_OPENAI = "openai"
    ENDPOINT_OLLAMA = "ollama"

    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    description = models.TextField(blank=True)
    avatar = models.ImageField(upload_to="sabbia/avatars/", null=True, blank=True)
    system_prompt = models.TextField(
        default="You are a helpful, concise assistant."
    )
    endpoint_type = models.CharField(max_length=40, choices=ENDPOINT_CHOICES)
    connector = models.ForeignKey(
        AgentConnector,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="agents",
        help_text="Holds encrypted credentials for API-key endpoints (e.g. OpenAI).",
    )
    model_name = models.CharField(
        max_length=120, blank=True, help_text="e.g. gpt-4.1-mini, qwen3:1.7b"
    )
    endpoint_config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Non-secret per-endpoint config: base_url, timeout, ...",
    )
    temperature = models.FloatField(default=0.2)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class PlatformChatbot(models.Model):
    """Links a core.Platform to the agent shown in its floating chat widget."""

    platform = models.OneToOneField(
        "core.Platform", on_delete=models.CASCADE, related_name="chatbot"
    )
    agent = models.ForeignKey(
        Agent, on_delete=models.PROTECT, related_name="platform_links"
    )
    is_enabled = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Platform chatbot"
        verbose_name_plural = "Platform chatbots"

    def __str__(self):
        return f"{self.platform} → {self.agent}"


class Conversation(models.Model):
    agent = models.ForeignKey(
        Agent, on_delete=models.CASCADE, related_name="conversations"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sabbia_conversations",
    )
    title = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title or f"Conversation {self.pk}"


class ChatMessage(models.Model):
    ROLE_USER = "user"
    ROLE_ASSISTANT = "assistant"
    ROLE_CHOICES = [(ROLE_USER, "User"), (ROLE_ASSISTANT, "Assistant")]

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.role}: {self.content[:40]}"
