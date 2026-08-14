"""Three tables, two kinds of question, one set of rules.

``Question`` is a poll OR a formal vote — the difference is the ``kind`` plus
the three policies it carries (see :mod:`toto.polls.core`), never a second set
of tables. Two model graphs for the same shape is how a "shared engine" becomes
two engines that drift.

**Scope is a soft pointer, on purpose.** A question may belong to a Forum room
or to a company, and this app must not import either: forum ships in a wheel
this one cannot depend on, and Business Center is a host app. So a scope is a
``(scope_type, scope_id)`` pair with an index, and each consumer asks for its
own. That is the same trade ``AiRun.workflow_run_id`` makes, and the same
reason: a real ForeignKey here would make an optional app mandatory.

**The scope is also the privacy boundary.** Business Center's requirement is
that Company A's votes are a completely separate set from Company B's — so
every query goes through :meth:`QuestionQuerySet.in_scope`, and there is no way
to list questions without naming a scope or explicitly asking for the global
ones.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from .core import Revisability, Visibility

#: Scope types. A question with GLOBAL scope belongs to the platform and shows
#: up in the standalone Polls app; anything else belongs to its owner and is
#: invisible outside it.
SCOPE_GLOBAL = ""
SCOPE_FORUM = "forum.channel"
SCOPE_COMPANY = "portfolio.company"


class Kind(models.TextChoices):
    POLL = "poll", _("Poll")
    VOTE = "vote", _("Vote")


class Status(models.TextChoices):
    OPEN = "open", _("Open")
    CLOSED = "closed", _("Closed")
    CANCELLED = "cancelled", _("Cancelled")


class QuestionQuerySet(models.QuerySet):
    def in_scope(self, scope_type: str = SCOPE_GLOBAL, scope_id: str = ""):
        """Questions belonging to exactly one scope.

        The ONLY door. Company A must never see Company B's votes, and the way
        to guarantee that is to make "all questions" impossible to ask for by
        accident rather than to remember a filter at each call site.
        """
        return self.filter(scope_type=scope_type, scope_id=str(scope_id or ""))

    def polls(self):
        return self.filter(kind=Kind.POLL)

    def votes(self):
        return self.filter(kind=Kind.VOTE)

    def open_now(self):
        now = timezone.now()
        return self.filter(status=Status.OPEN, opens_at__lte=now,
                           closes_at__gt=now)


class Question(models.Model):
    """A question put to an electorate. A poll or a formal vote."""

    kind = models.CharField(max_length=8, choices=Kind.choices,
                            default=Kind.POLL)

    title = models.CharField(max_length=150)
    question_text = models.CharField(max_length=300)
    body = models.TextField(blank=True, help_text=_(
        "The case for and against, for a formal vote. Shown above the options."))
    slug = models.SlugField(max_length=170, blank=True)

    # -- where it belongs ---------------------------------------------------
    #: "" for a platform-wide question; otherwise "forum.channel" /
    #: "portfolio.company". Never a ForeignKey — see the module docstring.
    scope_type = models.CharField(max_length=40, blank=True, db_index=True)
    scope_id = models.CharField(max_length=64, blank=True, db_index=True)

    # -- the window ---------------------------------------------------------
    opens_at = models.DateTimeField(default=timezone.now)
    closes_at = models.DateTimeField(null=True, blank=True, help_text=_(
        "When voting ends. A question with no closing time stays open until "
        "somebody closes it."))
    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.OPEN)
    closed_at = models.DateTimeField(null=True, blank=True)

    # -- the rules ----------------------------------------------------------
    revisability = models.CharField(max_length=8, choices=Revisability.CHOICES,
                                    default=Revisability.OPEN)
    visibility = models.CharField(max_length=20, choices=Visibility.CHOICES,
                                  default=Visibility.LIVE)

    # -- audit --------------------------------------------------------------
    # Who made it and when, kept for a formal vote's sake. A poll never needs
    # this and pays one nullable FK for it, which is cheaper than a second
    # table that exists only for votes.
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                   blank=True, on_delete=models.SET_NULL,
                                   related_name="questions_opened")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    metadata = models.JSONField(default=dict, blank=True)

    objects = QuestionQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # The scope query is the one every consumer makes.
            models.Index(fields=["scope_type", "scope_id", "-created_at"]),
            models.Index(fields=["kind", "status"]),
            models.Index(fields=["closes_at"]),
        ]
        constraints = [
            # Slugs are unique WITHIN a scope, not globally. The old app made
            # them globally unique, which meant two companies could not both
            # have a "budget-2027" vote — exactly the cross-tenant collision
            # the scoping exists to prevent.
            models.UniqueConstraint(fields=["scope_type", "scope_id", "slug"],
                                    name="uniq_question_slug_per_scope"),
        ]
        verbose_name = _("question")
        verbose_name_plural = _("questions")

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._unique_slug()
        # A formal vote is FINAL, and not by convention. Revisability is the
        # difference between a ballot and a preference, and leaving it to each
        # caller to remember means one that forgets produces a "vote" whose
        # ballots can be rewritten until it closes — which is precisely the bug
        # the old polls app shipped, in a view that used update_or_create.
        # Something revisable is a poll; that is what the two kinds ARE.
        if self.kind == Kind.VOTE:
            self.revisability = Revisability.FINAL
        super().save(*args, **kwargs)

    def _unique_slug(self) -> str:
        base = slugify(self.title) or "question"
        slug, counter = base, 1
        siblings = Question.objects.in_scope(self.scope_type, self.scope_id)
        while siblings.filter(slug=slug).exclude(pk=self.pk).exists():
            counter += 1
            slug = f"{base}-{counter}"
        return slug

    # -- state --------------------------------------------------------------

    @property
    def is_open(self) -> bool:
        """Open by the clock AND by its status. Read, never stored.

        A question closes when its time passes whether or not anything ran —
        no sweep, no task, no cron. A deadline enforced by a background job is a
        deadline that quietly does not apply when the worker is down.
        """
        now = timezone.now()
        if self.status != Status.OPEN or self.opens_at > now:
            return False
        return self.closes_at is None or self.closes_at > now

    @property
    def has_closed(self) -> bool:
        return not self.is_open

    def close(self, *, when=None) -> None:
        """Close it for good. Idempotent."""
        if self.status == Status.CLOSED:
            return
        self.status = Status.CLOSED
        self.closed_at = when or timezone.now()
        self.save(update_fields=["status", "closed_at", "updated_at"])

    @property
    def is_formal(self) -> bool:
        return self.kind == Kind.VOTE


class Choice(models.Model):
    """One answer somebody may pick."""

    question = models.ForeignKey(Question, on_delete=models.CASCADE,
                                 related_name="choices")
    label = models.CharField(max_length=60)
    text = models.CharField(max_length=300, blank=True)
    #: Free numeric value, for scoring and for ordering a scale. Not used by
    #: the tally, which counts weight — this is the option's meaning, not its
    #: strength.
    value = models.IntegerField(default=0)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "label"]
        constraints = [
            models.UniqueConstraint(fields=["question", "label"],
                                    name="uniq_choice_label_per_question"),
        ]

    def __str__(self):
        return f"{self.label}: {self.text}" if self.text else self.label


class Ballot(models.Model):
    """One voter's answer.

    **The weight is copied in at cast time and never recomputed.** Shares
    change hands and room membership changes; a tally that recomputed weights
    on read would rewrite the past every time somebody opened the page.
    """

    question = models.ForeignKey(Question, on_delete=models.CASCADE,
                                 related_name="ballots")
    choice = models.ForeignKey(Choice, on_delete=models.CASCADE,
                               related_name="ballots")
    voter = models.ForeignKey(settings.AUTH_USER_MODEL,
                              on_delete=models.CASCADE,
                              related_name="ballots")
    weight = models.PositiveIntegerField(default=1)

    cast_at = models.DateTimeField(auto_now_add=True)
    #: Set when a revisable answer is changed. Null on a formal ballot, always,
    #: which is what makes "this was never altered" a fact you can read off the
    #: row rather than a claim about the code.
    revised_at = models.DateTimeField(null=True, blank=True)
    revisions = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["cast_at"]
        constraints = [
            models.UniqueConstraint(fields=["question", "voter"],
                                    name="uniq_ballot_per_voter"),
        ]
        indexes = [
            models.Index(fields=["question", "choice"]),
        ]

    def __str__(self):
        return f"{self.voter} → {self.choice.label} ({self.weight})"
