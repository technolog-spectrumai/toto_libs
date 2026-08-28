"""Attributable voting: a frozen roll, propositions, ballots, a tally.

Revived from irena's `toto.voting`. The rules — quorum basis, majority basis,
abstention handling, threshold mode — and the arithmetic that reads them are
the originals. Two things changed, and both are what the brief asks for.

**The roll is soft.** Irena's voting had foreign keys into `company.Party` and
read weights straight off `company.ShareHolding`, which is why it could only
ever be a company's voting app. Here a `RollEntry` carries a *reference* — a
type, a uid and a name — and a weight, and this app never resolves it. Who is
eligible is decided outside, frozen in at open time, and never looked up again.
That is what lets shareholders, company members and hand-picked people all be
electorates, and it is what keeps this app reusable.

**A ballot can be changed.** Irena refused a second ballot. Here the earlier one
is superseded and kept: every ballot ever cast stays readable, exactly one is
active per voter and proposition, and only the active one counts. A partial
unique constraint holds that invariant at the database rather than in a view.

**Voting is never secret.** Every ballot names its voter, and there is no field
anywhere here that could hide one.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from toto.core.domain import DomainEntity


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------


class QuorumBasis(models.TextChoices):
    WEIGHT = "weight", "Represented voting weight"
    MEMBERS = "members", "Represented voters"


class MajorityBasis(models.TextChoices):
    CAST = "cast", "Ballots cast"
    DECISIVE = "decisive", "For and against votes"
    PRESENT = "present", "Present voting weight"
    ELIGIBLE = "eligible", "All eligible voting weight"


class AbstentionRule(models.TextChoices):
    INCLUDE = "include", "Include in the majority denominator"
    EXCLUDE = "exclude", "Exclude from the majority denominator"
    AGAINST = "against", "Count as against"


class ThresholdMode(models.TextChoices):
    STRICT = "strict", "Must be more than the threshold"
    INCLUSIVE = "inclusive", "May equal the threshold"


class VotingConfiguration(DomainEntity):
    """Staff-maintained policy whose values are FROZEN onto each proposition.

    Editing a configuration must never change what an already-open vote
    requires — so `configuration_snapshot` copies these values onto the meeting
    and again onto each proposition, and the tally reads the copy.
    """

    scope_type = models.CharField(max_length=80, blank=True)
    scope_uid = models.UUIDField(null=True, blank=True)

    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180)
    description = models.TextField(blank=True)

    quorum_basis = models.CharField(max_length=16, choices=QuorumBasis.choices,
                                    default=QuorumBasis.WEIGHT)
    quorum_percent = models.DecimalField(max_digits=5, decimal_places=2,
                                         default=Decimal("50.00"))
    majority_basis = models.CharField(max_length=16, choices=MajorityBasis.choices,
                                      default=MajorityBasis.CAST)
    majority_percent = models.DecimalField(max_digits=5, decimal_places=2,
                                           default=Decimal("50.00"))
    abstention_rule = models.CharField(max_length=16, choices=AbstentionRule.choices,
                                       default=AbstentionRule.INCLUDE)
    threshold_mode = models.CharField(max_length=16, choices=ThresholdMode.choices,
                                      default=ThresholdMode.STRICT)

    is_default = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("scope_type", "scope_uid", "name")
        constraints = [
            models.UniqueConstraint(
                fields=["scope_type", "scope_uid", "slug"],
                name="bc_one_voting_config_slug_per_scope",
            ),
            models.UniqueConstraint(
                fields=["scope_type", "scope_uid"],
                condition=models.Q(is_default=True),
                name="bc_one_default_voting_config",
            ),
            models.CheckConstraint(
                check=models.Q(quorum_percent__gte=0) & models.Q(quorum_percent__lte=100),
                name="bc_quorum_percent_range",
            ),
            models.CheckConstraint(
                check=models.Q(majority_percent__gte=0) & models.Q(majority_percent__lte=100),
                name="bc_majority_percent_range",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name) or "configuration"
        return super().save(*args, **kwargs)

    def snapshot(self) -> dict:
        """The values a vote freezes. Strings, so a Decimal cannot drift."""
        return {
            "configuration_uid": str(self.uid),
            "configuration_name": self.name,
            "quorum_basis": self.quorum_basis,
            "quorum_percent": str(self.quorum_percent),
            "majority_basis": self.majority_basis,
            "majority_percent": str(self.majority_percent),
            "abstention_rule": self.abstention_rule,
            "threshold_mode": self.threshold_mode,
        }

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# The meeting
# ---------------------------------------------------------------------------


class MeetingStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"
    CANCELLED = "cancelled", "Cancelled"


class Meeting(DomainEntity):
    """The container a vote happens inside.

    Its owner is a soft `scope_type`/`scope_uid` pair, like a ledger's. This app
    never resolves it; `company/integration/voting.py` is the only module that
    knows what those strings mean.
    """

    scope_type = models.CharField(max_length=80, blank=True)
    scope_uid = models.UUIDField(null=True, blank=True)

    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=180)
    description = models.TextField(blank=True)

    status = models.CharField(max_length=16, choices=MeetingStatus.choices,
                              default=MeetingStatus.DRAFT)
    configuration = models.ForeignKey(VotingConfiguration, on_delete=models.PROTECT,
                                      null=True, blank=True, related_name="meetings")
    #: Frozen at open. The tally reads this, never the live configuration.
    configuration_snapshot = models.JSONField(default=dict, blank=True)

    #: The date the roll is taken as of. What "eligible" means is decided by
    #: whoever supplies the roll; this is the date they are told to use.
    record_date = models.DateField(default=timezone.localdate)

    scheduled_for = models.DateTimeField(null=True, blank=True)
    held_at = models.DateTimeField(null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    eligible_weight = models.DecimalField(max_digits=24, decimal_places=6,
                                          default=Decimal("0"))
    present_weight = models.DecimalField(max_digits=24, decimal_places=6,
                                         default=Decimal("0"))
    eligible_voters = models.PositiveIntegerField(default=0)
    present_voters = models.PositiveIntegerField(default=0)
    quorum_met = models.BooleanField(default=False)

    result_payload = models.JSONField(default=dict, blank=True)

    chaired_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True,
                                   related_name="bc_meetings_chaired")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-held_at", "-scheduled_for", "-created_at")
        constraints = [
            models.UniqueConstraint(
                fields=["scope_type", "scope_uid", "slug"],
                name="bc_one_meeting_slug_per_scope",
            ),
        ]
        indexes = [
            models.Index(fields=["scope_type", "scope_uid"], name="bc_meeting_scope_idx"),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title) or "meeting"
        return super().save(*args, **kwargs)

    @property
    def is_open(self):
        return self.status == MeetingStatus.OPEN

    def __str__(self):
        return self.title


# ---------------------------------------------------------------------------
# The frozen roll
# ---------------------------------------------------------------------------


class RollSource(models.TextChoices):
    SHAREHOLDERS = "shareholders", "Shareholders, by voting weight"
    MEMBERS = "members", "Company members, one vote each"
    SELECTED = "selected", "Manually selected people"


class AttendanceStatus(models.TextChoices):
    PRESENT = "present", "Present"
    ABSENT = "absent", "Absent"
    EXCLUDED = "excluded", "Excluded"


class RollEntry(DomainEntity):
    """One eligible voter, frozen.

    `voter_ref` and `voter_name` are a COPY, not a link. A roll has to keep
    meaning what it meant: if a shareholder sells up or a member leaves a month
    after the vote, the record of who was entitled to vote must not change with
    them. This is the same reason a ledger block freezes its payload.
    """

    meeting = models.ForeignKey(Meeting, on_delete=models.PROTECT, related_name="roll")
    source = models.CharField(max_length=24, choices=RollSource.choices,
                              default=RollSource.SELECTED)

    #: What the supplier called this voter. Opaque here — never resolved.
    voter_ref = models.CharField(max_length=255)
    voter_name = models.CharField(max_length=255)

    weight = models.DecimalField(max_digits=24, decimal_places=6,
                                 default=Decimal("1"))
    eligible = models.BooleanField(default=True)
    status = models.CharField(max_length=16, choices=AttendanceStatus.choices,
                              default=AttendanceStatus.ABSENT)
    represented_by = models.CharField(
        max_length=255, blank=True,
        help_text="Who is voting on this voter's behalf, if anyone.",
    )
    evidence_ref = models.CharField(max_length=255, blank=True)
    note = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True,
                                    related_name="bc_roll_recorded")
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("meeting", "voter_name")
        verbose_name_plural = "roll entries"
        constraints = [
            models.UniqueConstraint(fields=["meeting", "voter_ref"],
                                    name="bc_one_roll_entry_per_voter"),
            models.CheckConstraint(check=models.Q(weight__gte=0),
                                   name="bc_roll_weight_non_negative"),
        ]

    def save(self, *args, **kwargs):
        allow_update = kwargs.pop("_allow_update", False)
        if self.pk and not allow_update:
            previous = type(self).objects.select_related("meeting").get(pk=self.pk)
            frozen = (
                previous.meeting.status in {MeetingStatus.CLOSED, MeetingStatus.CANCELLED}
                or Ballot.objects.filter(roll_entry_id=previous.pk).exists()
            )
            identity_or_weight_changed = any((
                previous.voter_ref != self.voter_ref,
                previous.voter_name != self.voter_name,
                previous.weight != self.weight,
                previous.eligible != self.eligible,
            ))
            if frozen and identity_or_weight_changed:
                raise ValidationError(
                    "Who was entitled to vote, and with what weight, is frozen "
                    "once a ballot has been cast."
                )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if Ballot.objects.filter(roll_entry_id=self.pk).exists():
            raise ValidationError("A voter who has cast a ballot cannot be removed.")
        # The meeting's CURRENT status, for the reason Proposition.delete gives.
        current = Meeting.objects.filter(pk=self.meeting_id).values_list(
            "status", flat=True).first()
        if current is not None and current != MeetingStatus.DRAFT:
            raise ValidationError("The roll is frozen once the meeting opens.")
        return super().delete(*args, **kwargs)

    @property
    def is_present(self):
        return self.status == AttendanceStatus.PRESENT

    def __str__(self):
        return f"{self.voter_name} ({self.weight})"


# ---------------------------------------------------------------------------
# The propositions
# ---------------------------------------------------------------------------


class ProposalStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"
    CANCELLED = "cancelled", "Cancelled"


class Proposition(DomainEntity):
    """What is being voted on. Frozen the moment voting opens on it."""

    meeting = models.ForeignKey(Meeting, on_delete=models.PROTECT,
                                related_name="propositions")
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=180)
    resolution_text = models.TextField()
    rationale = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=1)

    #: An attachment travels as a REFERENCE plus a HASH, never as a copy. The
    #: hash is what makes the reference meaningful: a vault file can be
    #: replaced, and a proposition that pointed at "the 2027 plan" without
    #: saying which bytes it meant would be a vote on a moving target.
    attachment_ref = models.CharField(max_length=255, blank=True)
    attachment_hash = models.CharField(max_length=128, blank=True)
    attachment_label = models.CharField(max_length=255, blank=True)

    status = models.CharField(max_length=16, choices=ProposalStatus.choices,
                              default=ProposalStatus.DRAFT)
    configuration_snapshot = models.JSONField(default=dict, blank=True)
    result_payload = models.JSONField(default=dict, blank=True)

    #: The block this decision became, once finalized. A uid, not a foreign
    #: key — `toto.ledger` must not gain a reverse relation into this app.
    block_uid = models.UUIDField(null=True, blank=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True,
                                   related_name="bc_propositions_created")
    opened_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("meeting", "order")
        constraints = [
            models.UniqueConstraint(fields=["meeting", "slug"],
                                    name="bc_one_proposition_slug_per_meeting"),
            models.UniqueConstraint(fields=["meeting", "order"],
                                    name="bc_one_proposition_order_per_meeting"),
        ]

    def save(self, *args, **kwargs):
        allow_update = kwargs.pop("_allow_update", False)
        if not self.slug:
            self.slug = slugify(self.title) or "proposition"
        if not self.pk and self.meeting.status in {MeetingStatus.CLOSED,
                                                   MeetingStatus.CANCELLED}:
            raise ValidationError("A closed meeting cannot accept new propositions.")
        if self.pk and not allow_update:
            previous = type(self).objects.get(pk=self.pk)
            if previous.status in {ProposalStatus.CLOSED, ProposalStatus.CANCELLED}:
                raise ValidationError("A finalized proposition is immutable.")
            if previous.status != ProposalStatus.DRAFT:
                instrument_changed = any((
                    previous.title != self.title,
                    previous.slug != self.slug,
                    previous.resolution_text != self.resolution_text,
                    previous.rationale != self.rationale,
                    previous.attachment_ref != self.attachment_ref,
                    previous.attachment_hash != self.attachment_hash,
                    previous.configuration_snapshot != self.configuration_snapshot,
                ))
                if instrument_changed:
                    raise ValidationError(
                        "What is being voted on, and the rules it is voted under, "
                        "are frozen once voting opens."
                    )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # Reads the CURRENT status, not this instance's. A guard that trusted
        # an in-memory value would let anybody holding an object from before
        # the vote opened delete it afterwards — and objects are held across
        # requests all the time.
        current = type(self).objects.filter(pk=self.pk).values_list(
            "status", flat=True).first()
        if current is not None and current != ProposalStatus.DRAFT:
            raise ValidationError("A proposition that entered voting cannot be deleted.")
        return super().delete(*args, **kwargs)

    @property
    def is_final(self):
        return self.status == ProposalStatus.CLOSED

    def __str__(self):
        return self.title


# ---------------------------------------------------------------------------
# The ballots
# ---------------------------------------------------------------------------


class BallotChoice(models.TextChoices):
    FOR = "for", "For"
    AGAINST = "against", "Against"
    ABSTAIN = "abstain", "Abstain"


class Ballot(DomainEntity):
    """One vote cast, by a named voter. Never secret, never deleted.

    A voter who changes their mind gets a NEW ballot; the previous one is
    marked inactive and kept. `bc_one_active_ballot` is a partial unique
    constraint over `(proposition, roll_entry)` where `active` — so the
    "exactly one live ballot" rule holds even against two concurrent requests
    that both read no existing ballot.
    """

    proposition = models.ForeignKey(Proposition, on_delete=models.PROTECT,
                                    related_name="ballots")
    roll_entry = models.ForeignKey(RollEntry, on_delete=models.PROTECT,
                                   related_name="ballots")

    choice = models.CharField(max_length=16, choices=BallotChoice.choices)
    weight = models.DecimalField(max_digits=24, decimal_places=6)

    #: False once a later ballot supersedes this one. The tally counts only
    #: active ballots; the history keeps all of them.
    active = models.BooleanField(default=True)
    supersedes = models.OneToOneField("self", on_delete=models.PROTECT, null=True,
                                      blank=True, related_name="superseded_by")

    cast_at = models.DateTimeField(auto_now_add=True)
    cast_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                null=True, blank=True, related_name="bc_ballots_cast")
    cast_by_ref = models.CharField(max_length=255, blank=True)

    #: Evidence that the person casting was who they said, and had just said so.
    #: `confirmed_at` is the moment they re-confirmed, not the session's age —
    #: see `services.lifecycle.cast_ballot`.
    confirmed_at = models.DateTimeField(null=True, blank=True)
    auth_evidence = models.JSONField(default=dict, blank=True)

    note = models.TextField(blank=True)

    class Meta:
        ordering = ("proposition", "cast_at")
        constraints = [
            models.UniqueConstraint(
                fields=["proposition", "roll_entry"],
                condition=models.Q(active=True),
                name="bc_one_active_ballot",
            ),
            models.CheckConstraint(check=models.Q(weight__gte=0),
                                   name="bc_ballot_weight_non_negative"),
        ]
        indexes = [
            models.Index(fields=["proposition", "active"], name="bc_ballot_active_idx"),
        ]

    def save(self, *args, **kwargs):
        """Immutable, with exactly one exception: being superseded.

        `_supersede=True` is the only path that may write to an existing
        ballot, and it may only ever set `active` to False. Anything else about
        a cast ballot is history.
        """
        superseding = kwargs.pop("_supersede", False)
        if self.pk and not superseding:
            raise ValidationError("Ballots are immutable. Cast a new one instead.")
        if superseding and self.active:
            raise ValidationError("Superseding a ballot means deactivating it.")
        if self.roll_entry_id and self.proposition_id:
            if self.roll_entry.meeting_id != self.proposition.meeting_id:
                raise ValidationError("The voter must be on this meeting's roll.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "Ballots cannot be deleted. A changed vote supersedes; it does not erase."
        )

    @property
    def voter_name(self):
        return self.roll_entry.voter_name

    def __str__(self):
        return f"{self.roll_entry.voter_name} {self.choice} ({self.weight})"
