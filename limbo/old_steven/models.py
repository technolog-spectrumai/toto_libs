from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from toto.api.models import ApiConnector
from toto.steven.services.provider_strategies import PROVIDER_CHOICES as _PROVIDER_CHOICES
from toto.steven.services.connection_testers import test_connection as _test_connection


class AgentConnector(ApiConnector):
    """Agent connector driven by the connection-tester registry.

    Supported providers and their connection logic live in
    services/provider_strategies.py. Adding a provider there is the only
    change needed — this model stays untouched.
    """

    OPENAI = ApiConnector.PROVIDER_OPENAI
    OLLAMA = "ollama"

    PROVIDER_CHOICES = _PROVIDER_CHOICES

    class Meta(ApiConnector.Meta):
        pass

    def clean(self):
        super().clean()
        valid = {p for p, _ in self.PROVIDER_CHOICES}
        if self.provider not in valid:
            raise ValidationError(
                {"provider": f"Provider must be one of: {', '.join(sorted(valid))}."}
            )

    is_slow = models.BooleanField(
        default=True,
        help_text=(
            "When enabled (default), inference is queued via Celery and the "
            "workflow engine. Disable only for connectors with sub-second latency."
        ),
    )

    def runtime_environment(self):
        """Returns credential dict. Requires a vault session to resolve; raises RuntimeError if unavailable."""
        raise RuntimeError(
            f'Connector "{self.name}" requires a Gervazy vault session to resolve credentials. '
            "Use build_auth_headers(vault_session=...) instead."
        )

    def test_connection(self, api_key: str = "", timeout: int = 10) -> dict:
        """Dispatch to the registered strategy for self.provider."""
        return _test_connection(self, api_key, timeout)


class AgentProfile(models.Model):
    """A configurable AI agent Steven can run."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="steven_agent_profile",
    )
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True)
    description = models.TextField(blank=True)
    avatar = models.ImageField(upload_to="steven/agent_avatars/", null=True, blank=True)
    system_prompt = models.TextField(
        default="You are Steven, a helpful AI agent manager. Be accurate, concise, and safe."
    )
    model_name = models.CharField(
        max_length=120,
        default=getattr(settings, "DEFAULT_AGENT_MODEL", "openai:gpt-4.1-mini"),
    )
    connector = models.ForeignKey(
        AgentConnector,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agents",
    )
    temperature = models.FloatField(default=0.2)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class AgentTool(models.Model):
    """Tools enabled for a specific agent. Tool implementations live in services/tools.py."""

    TOOL_CHOICES = [
        ("echo", "Echo"),
        ("calculator", "Calculator"),
        ("current_time", "Current time"),
        ("graph_cypher_qa", "Graph Cypher QA (ravioli Neo4j)"),
    ]

    agent = models.ForeignKey(AgentProfile, on_delete=models.CASCADE, related_name="tools")
    key = models.CharField(max_length=80, choices=TOOL_CHOICES)
    enabled = models.BooleanField(default=True)

    class Meta:
        unique_together = [("agent", "key")]
        ordering = ["agent__name", "key"]

    def __str__(self):
        return f"{self.agent.name}: {self.key}"


class Conversation(models.Model):
    """A multi-turn chat thread between a user and an agent."""

    agent = models.ForeignKey(AgentProfile, on_delete=models.CASCADE, related_name="conversations")
    title = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title or f"Conversation #{self.pk}"


class ChatMessage(models.Model):
    """A single message in a Conversation."""

    ROLE_USER = "user"
    ROLE_ASSISTANT = "assistant"
    ROLE_CHOICES = [
        (ROLE_USER, "User"),
        (ROLE_ASSISTANT, "Assistant"),
    ]

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.role}: {self.content[:60]}"


class AgentRun(models.Model):
    """Stores user prompts and agent outputs for auditing and debugging."""

    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("running", "Running"),
        ("succeeded", "Succeeded"),
        ("failed", "Failed"),
    ]

    agent = models.ForeignKey(AgentProfile, on_delete=models.CASCADE, related_name="runs")
    user_prompt = models.TextField()
    result = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    error = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.agent.name} run #{self.pk or 'new'}"
