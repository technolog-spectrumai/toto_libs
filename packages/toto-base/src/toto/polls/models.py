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

import dataclasses

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent

from .core import Revisability, Visibility

#: Scope types. A question with GLOBAL scope belongs to the platform and shows
#: up in the standalone Polls app; anything else belongs to its owner and is
#: invisible outside it.
SCOPE_GLOBAL = ""
SCOPE_FORUM = "forum.channel"
#: Communities. A company is one KIND of community now (socialhub's org_type),
#: which is why this replaced "portfolio.company" when the Business Center was
#: retired: a company's votes are community votes, on the same engine.
SCOPE_COMMUNITY = "socialhub.community"


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
        # Must agree with Question.is_open: a null closes_at means "open until
        # somebody closes it", so it belongs in this set too.
        now = timezone.now()
        return self.filter(status=Status.OPEN, opens_at__lte=now).filter(
            models.Q(closes_at__isnull=True) | models.Q(closes_at__gt=now))


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
    #: "socialhub.community". Never a ForeignKey — see the module docstring.
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

    # -- the instrument (stages 5-7) ----------------------------------------
    #: The configured roll this vote FROZE FROM. Standing is never answered
    #: from this row — it is answered from the RollEntry snapshot — so
    #: SET_NULL: deleting an electorate cannot touch a past vote.
    electorate = models.ForeignKey("polls.Electorate", null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="questions")
    #: One sentence naming what deciding this MEANS. Shown under the
    #: question; locked once voting starts.
    decision_header = models.CharField(max_length=200, blank=True)
    #: Optional longer context, shown behind Details. Locked with the header.
    decision_comment = models.TextField(blank=True)
    #: The consensus rule, snapshotted AT OPEN — name and number both, so
    #: retuning or renaming a profile can never rewrite what this vote
    #: required.
    rule_name = models.CharField(max_length=80, blank=True)
    rule_percent = models.DecimalField(max_digits=5, decimal_places=2,
                                       null=True, blank=True)
    #: The quorum rule, snapshotted AT OPEN like the consensus rule: mode,
    #: name and number all copied, so retuning the rule changes future votes
    #: only. Blank mode = no quorum requirement was taken.
    quorum_mode = models.CharField(max_length=10, blank=True)
    quorum_name = models.CharField(max_length=80, blank=True)
    quorum_threshold = models.DecimalField(max_digits=14, decimal_places=2,
                                           null=True, blank=True)
    #: How the session is entitled to decide. Locked with the instrument.
    convening_mode = models.CharField(max_length=10, blank=True,
                                      default="formal")
    #: Facts about the session that deserve the record but not a model of
    #: their own — an arrival, a departure, an objection, a chair's ruling.
    #: Deliberately NOT locked with the header: notes describe what happened
    #: DURING the session, and a field that seals before the session ends
    #: cannot record it. It is frozen by the decision, like everything else.
    procedural_notes = models.TextField(blank=True)

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
        # Once voting has started, the instrument is fixed: the header, the
        # comment, the snapshotted rule and the electorate pointer may not
        # move under voters who already read them. Status changes (closing)
        # pass untouched.
        if self.pk is not None and self.kind == Kind.VOTE:
            was = Question.objects.filter(pk=self.pk).values(
                "decision_header", "decision_comment", "rule_name",
                "rule_percent", "electorate_id", "quorum_mode",
                "quorum_name", "quorum_threshold", "convening_mode").first()
            # "Voting has started" means the register is frozen — that is the
            # moment the instrument became addressable to voters — or that
            # somebody has already balloted. Not merely that opens_at passed:
            # freezing the register IS part of opening, and it saves the row.
            started = was is not None and (
                self.roll.exists() or self.ballots.exists())
            if started:
                changed = (
                    was["decision_header"] != self.decision_header
                    or was["decision_comment"] != self.decision_comment
                    or was["rule_name"] != self.rule_name
                    or was["rule_percent"] != self.rule_percent
                    or was["electorate_id"] != self.electorate_id
                    or was["quorum_mode"] != self.quorum_mode
                    or was["quorum_name"] != self.quorum_name
                    or was["quorum_threshold"] != self.quorum_threshold
                    or was["convening_mode"] != self.convening_mode)
                if changed:
                    raise ValueError(
                        "Voting has started; the header, comment, rules, "
                        "quorum, convening mode and electorate are locked.")
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


class Outcome(models.TextChoices):
    """What a count says, without judging it.

    Pass/fail against a threshold is a POLICY (a consensus profile, stage 7);
    the outcome here is arithmetic: somebody led, nobody led, or nobody came.
    """

    WINNER = "winner", _("Winner")
    TIE = "tie", _("Tie")
    NO_BALLOTS = "no_ballots", _("No ballots")


def compute_hash(content: dict, prev_hash: str = "") -> str:
    """Deterministic: sorted-key JSON of the content plus the previous hash.
    The company minute book's construction, verbatim."""
    import hashlib
    import json

    canonical = json.dumps(content, sort_keys=True, default=str)
    return hashlib.sha256((canonical + prev_hash).encode()).hexdigest()


@dataclasses.dataclass(frozen=True)
class ChainVerification:
    ok: bool
    checked: int
    first_bad_pk: int | None


class DecisionQuerySet(models.QuerySet):
    def in_scope(self, scope_type: str = SCOPE_GLOBAL, scope_id: str = ""):
        """Decisions belonging to exactly one scope — the ONLY door, for the
        same reason as :meth:`QuestionQuerySet.in_scope`: Company A's decided
        votes are not a filter away from Company B's, they are a different
        set."""
        return self.filter(scope_type=scope_type, scope_id=str(scope_id or ""))


class Decision(models.Model):
    """The recorded result of one formal vote. Written once, never edited.

    A Tally is recomputed on every request; a decision must not be — "the
    vote passed with 62%" has to stay true even if a ballot row is deleted
    years later. So everything the ledger shows lives in real columns here,
    and the full snapshot (tally rows, per-ballot audit list) lives in
    ``content``. The field is named ``content`` to match
    ``chainledger.ChainedRecord``: stage 6 promotes this table onto the hash
    chain by swapping the base class, not by migrating data.
    """

    question = models.OneToOneField(Question, on_delete=models.PROTECT,
                                    related_name="decision")

    # -- denormalized for the ledger: never joins through Question, and the
    #    row keeps saying what was decided even if the question is edited.
    scope_type = models.CharField(max_length=40, blank=True, db_index=True)
    scope_id = models.CharField(max_length=64, blank=True, db_index=True)
    title = models.CharField(max_length=150)
    outcome = models.CharField(max_length=12, choices=Outcome.choices)
    winner_label = models.CharField(max_length=60, blank=True)
    electorate_key = models.CharField(max_length=40, blank=True)
    electorate_size = models.PositiveIntegerField(default=0)
    total_ballots = models.PositiveIntegerField(default=0)
    total_weight = models.PositiveIntegerField(default=0)
    #: Fraction in [0, 1]; null when the electorate size is unknown (0).
    turnout = models.FloatField(null=True, blank=True)

    decided_at = models.DateTimeField(default=timezone.now)
    #: Who triggered the recording. Null means the clock did — the deadline
    #: passed and the first read materialized it.
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                   blank=True, on_delete=models.SET_NULL,
                                   related_name="decisions_recorded")

    #: The full snapshot: question, proposer, window, electorate, tally rows,
    #: per-ballot audit list. The name matches ChainedRecord.content.
    content = models.JSONField(default=dict)

    #: The consensus verdict, when the vote snapshotted a rule: True adopted,
    #: False rejected, null when no rule applied.
    adopted = models.BooleanField(null=True, blank=True)

    # -- the ledger chain (stage 6) -----------------------------------------
    #: sha256 over the canonical content plus the previous hash, one chain
    #: PER SCOPE — the same construction the company minute book uses, and
    #: the same trust model: raw-SQL tampering stays possible and becomes
    #: DETECTABLE, because every later hash stops verifying.
    content_hash = models.CharField(max_length=64, blank=True, editable=False)
    prev_hash = models.CharField(max_length=64, blank=True, editable=False)

    objects = DecisionQuerySet.as_manager()

    class Meta:
        ordering = ["-decided_at"]
        indexes = [
            models.Index(fields=["scope_type", "scope_id", "-decided_at"]),
            models.Index(fields=["outcome"]),
        ]
        verbose_name = _("decision")
        verbose_name_plural = _("decisions")

    def __str__(self):
        return f"{self.title}: {self.get_outcome_display()}"

    def save(self, *args, **kwargs):
        # ChainedRecord's idiom: a decision is appended, never amended — and
        # appended ON THE CHAIN: linked by pk order within its scope, under
        # select_for_update so two writers cannot fork it.
        if self.pk is not None:
            raise ValueError(
                "A decision is never edited. Record a new question instead.")
        last = (Decision.objects.select_for_update()
                .filter(scope_type=self.scope_type, scope_id=self.scope_id)
                .order_by("-pk").first())
        self.prev_hash = last.content_hash if last else ""
        self.content_hash = compute_hash(self.content, self.prev_hash)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("A decision is never deleted; the ledger is history.")

    @classmethod
    def verify_chain(cls, scope_type: str = "", scope_id: str = ""):
        """Recompute one scope's chain and name the first broken entry."""
        prev = ""
        checked = 0
        for row in (cls.objects.filter(scope_type=scope_type,
                                       scope_id=str(scope_id or ""))
                    .order_by("pk").iterator()):
            expected = compute_hash(row.content, prev)
            if row.prev_hash != prev or row.content_hash != expected:
                return ChainVerification(ok=False, checked=checked,
                                         first_bad_pk=row.pk)
            prev = row.content_hash
            checked += 1
        return ChainVerification(ok=True, checked=checked, first_bad_pk=None)


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

    def save(self, *args, **kwargs):
        # Model-level backstop, not the primary guard: services.cast() refuses
        # first with a sentence (AlreadyCast). This catches the code path that
        # never went through services — the same defence-in-depth ChainedRecord
        # uses. Creation (pk is None) and poll revisions pass untouched.
        if self.pk is not None and \
                self.question.revisability == Revisability.FINAL:
            raise ValueError("A formal ballot is never altered.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # Individual deletes only; queryset .delete() and the CASCADE from
        # Question stay possible — the documented trust model. A decided
        # question is PROTECTed by its Decision anyway.
        if self.question.revisability == Revisability.FINAL:
            raise ValueError("A formal ballot is never deleted.")
        return super().delete(*args, **kwargs)


# -- electorates as data, the frozen register, consensus profiles -------------
from .electorate_models import (CONVENING_ASPECTS,  # noqa: E402,F401
                                ConsensusProfile, ConveningMode, Electorate,
                                ElectorateMember, Presence, QuorumRule,
                                RollEntry, VoteExclusion, VoteProcedure)

# -- quizzes: competence testing on the same scoping --------------------------
# Imported here so Django's migration autodetector sees them as polls models;
# the module carries its own docstring on what came from delta and what stayed.
from .quiz_models import (Quiz, QuizAnswer, QuizAttempt,  # noqa: E402,F401
                          QuizAttemptAnswer, QuizCertificate, QuizQuestion)

# -- metering (the polls.pdf metric's storage) -------------------------------


class PollsUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        verbose_name = _("Polls usage event")
        verbose_name_plural = _("Polls usage events")


class PollsQuotaPolicy(AbstractQuotaPolicy):
    events = PollsUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = _("Polls quota policy")
        verbose_name_plural = _("Polls quota policies")
