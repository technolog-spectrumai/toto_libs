import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from toto.api.models import ApiConnector


class AgentConnector(ApiConnector):
    """Agent connector. Supports OpenAI (real AI) and Rule-based (no API key, for dev/testing)."""

    OPENAI = ApiConnector.PROVIDER_OPENAI
    RULE_BASED = ApiConnector.PROVIDER_RULE_BASED

    PROVIDER_CHOICES = [
        (ApiConnector.PROVIDER_OPENAI, "OpenAI"),
        (ApiConnector.PROVIDER_RULE_BASED, "Rule-based (no API key)"),
    ]

    class Meta(ApiConnector.Meta):
        pass

    def clean(self):
        super().clean()
        if self.provider not in (self.OPENAI, self.RULE_BASED):
            raise ValidationError({"provider": "Only OpenAI and Rule-based connectors are supported."})

    def runtime_environment(self):
        """Returns credential dict. Requires a vault session to resolve; raises RuntimeError if unavailable."""
        raise RuntimeError(
            f'Connector "{self.name}" requires a Gervazy vault session to resolve credentials. '
            "Use build_auth_headers(vault_session=...) instead."
        )

    def test_connection(self, api_key: str, timeout: int = 10):
        """Test connectivity with an already-decrypted API key."""
        payload = json.dumps({
            "model": "gpt-4.1-mini",
            "messages": [{"role": "user", "content": "Reply with ok."}],
            "max_tokens": 8,
        }).encode("utf-8")
        request = Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "toto-studio-steven/1.0",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                if 200 <= response.status < 300:
                    return {"ok": True, "message": f"OpenAI connection succeeded (HTTP {response.status})."}
                return {"ok": False, "message": f"OpenAI connection returned HTTP {response.status}."}
        except HTTPError as exc:
            detail = exc.read(300).decode("utf-8", errors="replace").strip()
            message = f"OpenAI connection failed with HTTP {exc.code}."
            if detail:
                message = f"{message} {detail}"
            return {"ok": False, "message": message}
        except URLError as exc:
            return {"ok": False, "message": f"OpenAI connection failed: {exc.reason}"}
        except TimeoutError:
            return {"ok": False, "message": f"OpenAI connection timed out after {timeout} seconds."}


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
    uses_encrypted_chat = models.BooleanField(
        default=False,
        help_text="When enabled, Enigma chat routes this agent through the MLS encrypted session layer.",
    )
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
