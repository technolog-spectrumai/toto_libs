"""Steven — how it is configured, who it is, and what it ran.

**One provider row is active; several may exist.** That is
``jess.EmailProvider``'s shape, and jess is the only place in this tree that has
it, so it is copied rather than re-derived: a console row can sit beside a
working one, switching is flipping a flag, and the row you switched away from is
still there to switch back to. :class:`AiAgent` follows the same rule for the
same reason — a persona you are drafting sits beside the one answering.

**The key is not a column.** It is a ``gervazy.EncryptedSecret`` in steven's own
strongbox — see :mod:`toto.steven.vault`. ``deploy.py`` copies config env into
``.env`` with no redaction, which is why the environment is the wrong place for
it.

**A provider and an agent are different questions.** The provider is *where
completions come from* — a URL, a model name, a key, a timeout. The agent is
*who is answering* — a name, a persona, the house's rules about how to write.
They are two tables because they change for unrelated reasons and by unrelated
people: swapping to a cheaper model must not disturb a tone somebody spent an
afternoon on, and editing that tone must never be a way to touch an API key.
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


class AiAgent(models.Model):
    """Who the assistant is, and how the house wants it to write.

    Everything here ends up in the **system message**, and nowhere else — see
    ``toto.core.ai_surfaces.compose_system``, which decides the order and keeps
    the action's own output rule last so no amount of tuning here can make the
    assistant start returning code fences into somebody's document.

    **The icon is a Font Awesome class, not an uploaded avatar.** The parked app
    had an ``ImageField``; an upload here would mean a media path, a storage
    question and an untrusted image nobody scans, all so a settings row can have
    a picture. Every other identity on this platform is drawn from the same icon
    set, and this one is not the exception worth building an upload door for.
    """

    name = models.CharField(max_length=60, default="Steven", help_text=_(
        "What it calls itself. Shown on the panel, and told to the model — so "
        "'who are you' gets this answer rather than the provider's."))
    icon = models.CharField(max_length=80,
                            default="fa-solid fa-wand-magic-sparkles",
                            help_text=_("A Font Awesome class."))
    tagline = models.CharField(max_length=160, blank=True, help_text=_(
        "One line, under the name. Not sent to the model."))
    description = models.TextField(blank=True, help_text=_(
        "What this assistant is for, in the user's words. Shown on the console. "
        "Not sent to the model."))

    persona = models.TextField(blank=True, help_text=_(
        "One or two sentences telling the model what it is. Sent on EVERY "
        "call, so length here is a bill, not a detail."))
    language = models.CharField(max_length=60, blank=True, help_text=_(
        "Leave blank to answer in the language of the text — the right default "
        "when people write in several. A value here overrides that."))
    house_rules = models.TextField(blank=True, help_text=_(
        "Rules that apply to every action: what never to invent, how blunt to "
        "be, what to do when unsure."))
    kind_notes = models.JSONField(default=dict, blank=True, help_text=_(
        "kind → an extra note for surfaces of that kind only (prose, code, "
        "latex, sheet, cells). Tuning LaTeX answers must not change prose ones."))

    active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-active", "-updated_at"]
        verbose_name = _("AI agent")
        verbose_name_plural = _("AI agents")

    def __str__(self):
        return f"{self.name}{' (active)' if self.active else ''}"

    def save(self, *args, **kwargs):
        """One active row, the same transaction-bound way ``AiProvider`` does it."""
        with transaction.atomic():
            super().save(*args, **kwargs)
            if self.active:
                AiAgent.objects.filter(active=True).exclude(pk=self.pk).update(
                    active=False)

    @classmethod
    def current(cls):
        """The active agent, or None. Read per call — never cached."""
        return cls.objects.filter(active=True).order_by("-updated_at").first()

    def as_voice(self):
        """This row as the value ``compose_system`` takes."""
        from toto.core.ai_surfaces import AgentVoice

        return AgentVoice(
            name=self.name, persona=self.persona, language=self.language,
            house_rules=self.house_rules,
            # A hand-edited JSON column can hold anything; a bad one must not
            # take the assistant down, so a non-mapping degrades to no notes.
            kind_notes=self.kind_notes if isinstance(self.kind_notes, dict) else {},
        )

    @classmethod
    def voice(cls):
        """The active agent's voice, or None when nobody has configured one."""
        agent = cls.current()
        return agent.as_voice() if agent else None


class AiPersonalization(models.Model):
    """One user's standing note to the assistant. Sent with EVERY question.

    The per-person counterpart of :class:`AiAgent`: the operator's voice says
    who the assistant is for everyone, this says how one person wants to be
    answered ("keep it short", "I write Django", "answer in Polish"). It lands
    in the system message under the house rules and ABOVE the action's rule —
    ``toto.core.ai_surfaces.compose_system`` keeps the output rule last, so a
    user's preferences can shape tone and can never displace the answer shape.

    Capped because it is a bill: like the persona, it rides on every single
    call, and an essay here would be paid for token by token, question after
    question.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL,
                                on_delete=models.CASCADE,
                                related_name="ai_personalization")
    text = models.TextField(blank=True, max_length=2000, help_text=_(
        "Sent with every question you ask, so it counts toward what each "
        "question costs. Keep it short."))
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("AI personalization")
        verbose_name_plural = _("AI personalizations")

    def __str__(self):
        return f"Personalization for {self.user}"

    @classmethod
    def text_for(cls, user) -> str:
        """The user's note, or "". Never raises — same stance as
        ``services._system_chars``: an assistant that answers without the note
        beats one that refuses because a lookup hiccuped."""
        try:
            if user is None or not getattr(user, "is_authenticated", False):
                return ""
            row = cls.objects.filter(user=user).only("text").first()
            return row.text if row else ""
        except Exception:  # noqa: BLE001
            return ""


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
    #: Wall time of the provider call itself, in milliseconds. 0 for a run that
    #: never reached the provider; a slow FAILURE records its duration too —
    #: a timeout is a diagnostic fact, not an absence of one.
    duration_ms = models.PositiveIntegerField(default=0)
    #: Snapshots, not FKs: statistics group by what answered AT THE TIME, and
    #: renaming or deleting an agent later must not rewrite last month's rows.
    agent_label = models.CharField(max_length=60, blank=True)
    provider_label = models.CharField(max_length=120, blank=True)
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
               model_used: str = "", duration_ms: int | None = None) -> None:
        self.status = status
        self.result = result
        self.error = error[:4000]
        self.model_used = model_used or self.model_used
        if usage:
            self.prompt_tokens = int(usage.get("prompt_tokens") or 0)
            self.completion_tokens = int(usage.get("completion_tokens") or 0)
            self.total_tokens = int(usage.get("total_tokens") or 0)
        if duration_ms is not None:
            self.duration_ms = int(duration_ms)
        self.finished_at = timezone.now()
        # The label snapshots are assigned on the instance by services.execute
        # before any finish path runs; listed here or they silently vanish.
        self.save(update_fields=[
            "status", "result", "error", "model_used", "prompt_tokens",
            "completion_tokens", "total_tokens", "duration_ms", "agent_label",
            "provider_label", "finished_at"])


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
