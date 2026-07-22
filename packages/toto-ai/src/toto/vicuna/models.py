from django.db import models


class OllamaServer(models.Model):
    """An Ollama deployment. base_url here should match AgentConnector.base_url."""

    name = models.CharField(max_length=120, unique=True)
    host = models.URLField(
        unique=True,
        default="http://localhost:11434",
        help_text="Ollama base URL, e.g. http://localhost:11434",
    )
    uses_gpu = models.BooleanField(
        default=False,
        help_text="Whether this Ollama instance runs on GPU.",
    )
    is_active = models.BooleanField(default=True)
    last_synced = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        gpu = " [GPU]" if self.uses_gpu else ""
        return f"{self.name}{gpu} ({self.host})"


class OllamaModel(models.Model):
    """A model that has been pulled on an OllamaServer."""

    server = models.ForeignKey(
        OllamaServer,
        on_delete=models.CASCADE,
        related_name="models",
    )
    name = models.CharField(max_length=200)
    size_bytes = models.BigIntegerField(
        null=True, blank=True,
        help_text="Size reported by Ollama /api/tags.",
    )
    last_seen = models.DateTimeField(
        auto_now=True,
        help_text="Last time this model appeared in /api/tags.",
    )

    class Meta:
        unique_together = [("server", "name")]
        ordering = ["server", "name"]

    def __str__(self):
        return f"{self.name} @ {self.server.name}"
