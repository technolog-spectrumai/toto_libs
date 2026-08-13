"""Steven — the assistant's two tables: how it is configured, and what it ran.

**One provider row is active; several may exist.** That is
``jess.EmailProvider``'s shape, and jess is the only place in this tree that has
it, so it is copied rather than re-derived: a console row can sit beside a
working one, switching is flipping a flag, and the row you switched away from is
still there to switch back to.

**The key is not a column.** It is a ``gervazy.EncryptedSecret`` in steven's own
strongbox — see :mod:`toto.steven.vault`. ``deploy.py`` copies config env into
``.env`` with no redaction, which is why the environment is the wrong place for
it.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent

#: What a call costs, metered. Two codes because two things vary independently:
#: how OFTEN somebody asks, and how BIG each ask turns out to be.
METRIC_REQUEST = "ai.request"
METRIC_TOKENS = "ai.tokens_1k"

#: The audit tag on the stored key.
PURPOSE_API_KEY = "ai_api_key"


class RunStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    RUNNING = "running", _("Running")
    SUCCESS = "success", _("Done")
    FAILED = "failed", _("Failed")


class AiProvider(models.Model):
    """Where completions come from. Several may exist; exactly one is active.

    **Only the OpenAI wire format is implemented, and ``base_url`` is what makes
    that enough.** Every serious provider speaks it, so pointing this row
    somewhere else is a configuration change rather than a second code path —
    which is the difference between "configurable" and "a plugin system nobody
    asked for".
    """

    label = models.CharField(max_length=120, help_text=_(
        "What an operator calls this row. 'OpenAI production', 'staging key'."))
    base_url = models.URLField(
        default="https://api.openai.com/v1",
        help_text=_("The OpenAI-compatible API root. Any provider speaking that "
                    "wire format works here without a code change."))
    model = models.CharField(max_length=120, default="gpt-4.1-mini")
    temperature = models.DecimalField(max_digits=3, decimal_places=2,
                                      default=Decimal("0.20"))
    max_output_tokens = models.PositiveIntegerField(default=1024, help_text=_(
        "The ceiling per answer. Also the WORST CASE this platform checks a "
        "wallet against before running anything — see services.affordable."))
    timeout = models.PositiveIntegerField(default=60, help_text=_("Seconds."))

    secret = models.ForeignKey(
        "gervazy.EncryptedSecret", null=True, blank=True,
        on_delete=models.PROTECT, related_name="steven_providers",
        help_text=_("Gervazy EncryptedSecret holding the API key."))

    active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-active", "-updated_at"]
        verbose_name = _("AI provider")
        verbose_name_plural = _("AI providers")

    def __str__(self):
        return f"{self.label}{' (active)' if self.active else ''}"

    def save(self, *args, **kwargs):
        """Persist, keeping the one-active-row invariant.

        Done here rather than with a partial unique index because a database
        constraint would make *deactivating in order to activate another* a
        two-step dance that can fail halfway. Same approach as
        ``jess.EmailProvider.save`` and the OIDC bundle import.
        """
        with transaction.atomic():
            super().save(*args, **kwargs)
            if self.active:
                AiProvider.objects.filter(active=True).exclude(pk=self.pk).update(
                    active=False)

    @classmethod
    def current(cls):
        """The active provider, or None. Read per call — never cached."""
        return cls.objects.filter(active=True).order_by("-updated_at").first()

    @property
    def is_usable(self) -> bool:
        return bool(self.active and self.secret_id)


class AiRun(models.Model):
    """One request to the model.

    The same shape every long job on this platform already has — status, an
    owner, stdout-ish output, an error, a task id, a workflow run and two
    timestamps — so the polling code, the stuck-run sweeper and the run page all
    work the way they do everywhere else.
    """

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name="ai_runs")
    #: Which editor asked. An AiSurface key — see toto.steven.surfaces.
    surface = models.CharField(max_length=64, blank=True)
    #: Which action on that surface ("improve", "shorten", …).
    action = models.CharField(max_length=64, blank=True)

    #: What the user selected. Stored so a result can be shown beside its input
    #: and so an audit can answer "what did we send them".
    source_text = models.TextField(blank=True)
    instruction = models.TextField(blank=True, help_text=_(
        "Free-text extra instruction, when the action takes one."))

    result = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=RunStatus.choices,
                              default=RunStatus.PENDING)
    error = models.TextField(blank=True)

    #: Filled from the API response. The whole basis of the charge.
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)

    model_used = models.CharField(max_length=120, blank=True)
    task_id = models.CharField(max_length=255, blank=True)
    #: Not an FK: a real one would make toto.workflows a hard dependency of this
    #: app, and a host can run the assistant without the workflow engine's
    #: models being installed. Same reasoning as texlab.LatexRun.
    workflow_run_id = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner", "-created_at"]),
            models.Index(fields=["status"]),
        ]
        verbose_name = _("AI run")
        verbose_name_plural = _("AI runs")

    def __str__(self):
        return f"{self.owner} · {self.surface}/{self.action} · {self.status}"

    @property
    def is_finished(self) -> bool:
        return self.status in (RunStatus.SUCCESS, RunStatus.FAILED)

    @property
    def billable_units(self) -> Decimal:
        """Thousands of tokens, as a Decimal. 0 when nothing came back."""
        if not self.total_tokens:
            return Decimal("0")
        return (Decimal(self.total_tokens) / Decimal("1000")).quantize(Decimal("0.001"))

    def finish(self, *, status, result: str = "", error: str = "", usage=None,
               model_used: str = "") -> None:
        self.status = status
        self.result = result
        self.error = error[:4000]
        self.model_used = model_used or self.model_used
        if usage:
            self.prompt_tokens = int(usage.get("prompt_tokens") or 0)
            self.completion_tokens = int(usage.get("completion_tokens") or 0)
            self.total_tokens = int(usage.get("total_tokens") or 0)
        self.finished_at = timezone.now()
        self.save(update_fields=[
            "status", "result", "error", "model_used", "prompt_tokens",
            "completion_tokens", "total_tokens", "finished_at"])


# ---------------------------------------------------------------------------
# Metering
# ---------------------------------------------------------------------------
# The standard opt-in (toto.quota.models): each app owns its own pair, so the
# rows live in this app's tables and go away with it.

class StevenUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        verbose_name = "Steven usage event"
        verbose_name_plural = "Steven usage events"


class StevenQuotaPolicy(AbstractQuotaPolicy):
    events = StevenUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = "Steven quota policy"
        verbose_name_plural = "Steven quota policies"
