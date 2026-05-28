from __future__ import annotations

import hashlib
import json

from django.db import models
from django.utils import timezone


def _compute_hash(content: dict, prev_hash: str = "") -> str:
    data = json.dumps(content, sort_keys=True, default=str) + prev_hash
    return hashlib.sha256(data.encode()).hexdigest()


class AssemblyProposalType(models.TextChoices):
    RULE = "rule", "Rule"
    ASSET_TAX = "asset_tax", "Asset Transaction Tax"
    EMERGENCY_DECLARATION = "emg_declare", "Emergency Declaration"
    MAGISTRATE_ELECTION = "mag_elect", "Magistrate Election"
    IMPEACHMENT = "impeach", "Impeachment"
    SENATE_APPOINTMENT = "senate_appoint", "Senate Appointment"
    MOBILIZATION = "mobilization", "Mobilization Call"


class AssemblyStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    OPEN = "open", "Open"
    PENDING_SENATE = "pending_senate", "Pending Senate Review"
    PASSED = "passed", "Passed"
    REJECTED = "rejected", "Rejected"
    VETOED = "vetoed", "Vetoed by Senate"
    EXPIRED = "expired", "Expired"


class AssemblyVoteChoice(models.TextChoices):
    YES = "yes", "Yes"
    NO = "no", "No"
    ABSTAIN = "abstain", "Abstain"


class PollTaxPeriod(models.TextChoices):
    MONTHLY = "monthly", "Monthly"
    QUARTERLY = "quarterly", "Quarterly"
    YEARLY = "yearly", "Yearly"
    ONE_TIME = "one_time", "One-time"


class CommunityAssemblyConfig(models.Model):
    """
    Community-level assembly settings. Set by admin (founders / senior members).
    Quorum is the fraction of yes votes out of yes+no required to pass a proposal.
    """
    community = models.OneToOneField(
        "socialhub.Community",
        on_delete=models.CASCADE,
        related_name="assembly_config",
    )
    quorum_fraction = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        default="0.5100",
        help_text="Fraction of yes/(yes+no) votes required to pass a proposal (e.g. 0.51 = simple majority).",
    )
    is_bicameral = models.BooleanField(
        default=False,
        help_text="When True, proposals that pass the popular vote go to senate review before enactment. Senators are the community's senior members.",
    )

    class Meta:
        verbose_name = "Assembly configuration"
        verbose_name_plural = "Assembly configurations"

    def __str__(self):
        return f"{self.community} — quorum {float(self.quorum_fraction):.0%}"


class AssemblyProposal(models.Model):
    community = models.ForeignKey(
        "socialhub.Community",
        on_delete=models.CASCADE,
        related_name="assembly_proposals",
    )
    proposal_type = models.CharField(max_length=20, choices=AssemblyProposalType.choices)
    is_repeal = models.BooleanField(
        default=False,
        help_text="For rule proposals: True means this repeals/removes an existing rule.",
    )
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=AssemblyStatus.choices, default=AssemblyStatus.DRAFT)
    opened_by = models.ForeignKey(
        "people.Person", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="assembly_proposals_opened",
    )
    opens_at = models.DateTimeField(null=True, blank=True)
    closes_at = models.DateTimeField(null=True, blank=True)
    senate_deadline = models.DateTimeField(
        null=True, blank=True,
        help_text="Set when proposal passes popular vote and enters senate review. Senate must veto before this deadline or the proposal is enacted.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["community", "status"]),
            models.Index(fields=["proposal_type"]),
            models.Index(fields=["closes_at"]),
        ]

    def __str__(self):
        return self.title

    @property
    def is_open(self) -> bool:
        return (
            self.status == AssemblyStatus.OPEN
            and (not self.closes_at or timezone.now() < self.closes_at)
        )

    def tally(self) -> dict[str, int]:
        counts = {
            AssemblyVoteChoice.YES: 0,
            AssemblyVoteChoice.NO: 0,
            AssemblyVoteChoice.ABSTAIN: 0,
        }
        for v in self.votes.values_list("vote", flat=True):
            counts[v] = counts.get(v, 0) + 1
        return counts


class AssemblyVote(models.Model):
    proposal = models.ForeignKey(AssemblyProposal, on_delete=models.CASCADE, related_name="votes")
    voter = models.ForeignKey(
        "people.Person", on_delete=models.CASCADE, related_name="assembly_votes",
    )
    vote = models.CharField(max_length=10, choices=AssemblyVoteChoice.choices)
    voted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("proposal", "voter")]
        ordering = ["voted_at"]
        indexes = [
            models.Index(fields=["proposal"]),
            models.Index(fields=["voter"]),
        ]

    def __str__(self):
        return f"{self.voter} → {self.vote}"


class AssemblyDecision(models.Model):
    """
    Immutable ledger of enacted assembly decisions.
    Once created it must never be modified or deleted.
    Forms a hash-chain for integrity verification.
    """
    community = models.ForeignKey(
        "socialhub.Community",
        on_delete=models.PROTECT,
        related_name="assembly_decisions",
    )
    proposal = models.OneToOneField(
        AssemblyProposal,
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="decision",
    )
    decision_type = models.CharField(
        max_length=30,
        help_text="rule / transaction_fee / poll_tax / general_decision",
    )
    title = models.CharField(max_length=255)
    content = models.JSONField(help_text="Immutable decision payload.")
    content_hash = models.CharField(max_length=64, blank=True)
    prev_hash = models.CharField(max_length=64, blank=True, help_text="Hash of the previous decision in this community.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["community", "created_at"]),
            models.Index(fields=["decision_type"]),
        ]

    def __str__(self):
        return f"[{self.decision_type}] {self.title}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("AssemblyDecision is immutable — use create() only.")
        if not self.content_hash:
            prev = (
                AssemblyDecision.objects
                .filter(community=self.community)
                .order_by("-created_at")
                .first()
            )
            self.prev_hash = prev.content_hash if prev else ""
            self.content_hash = _compute_hash(self.content, self.prev_hash)
        super().save(*args, **kwargs)

    @classmethod
    def record(cls, *, community, proposal=None, decision_type: str, title: str, content: dict) -> "AssemblyDecision":
        return cls.objects.create(
            community=community,
            proposal=proposal,
            decision_type=decision_type,
            title=title,
            content=content,
        )


class CommunityRule(models.Model):
    community = models.ForeignKey(
        "socialhub.Community", on_delete=models.CASCADE, related_name="rules",
    )
    decision = models.ForeignKey(
        AssemblyDecision, on_delete=models.PROTECT, null=True, blank=True,
        related_name="enacted_rules",
    )
    title = models.CharField(max_length=255)
    text = models.TextField()
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["community", "created_at"]
        indexes = [models.Index(fields=["community", "active"])]

    def __str__(self):
        return self.title


class CommunityTransactionFee(models.Model):
    """
    Transaction fee set by admin or assembly decision.

    Two scopes:
    - asset=None  → blanket fee applied to every asset in this community
    - asset=<FK>  → per-asset adjustment (can be positive or negative bps)

    Effective fee for a given asset = blanket_fee_bps + asset_specific_fee_bps (floored at 0).
    """
    SET_BY_ADMIN = "admin"
    SET_BY_ASSEMBLY = "assembly"
    SET_BY_CHOICES = [(SET_BY_ADMIN, "Admin"), (SET_BY_ASSEMBLY, "Assembly")]

    community = models.ForeignKey(
        "socialhub.Community", on_delete=models.CASCADE, related_name="transaction_fees",
    )
    asset = models.ForeignKey(
        "assets.Asset", on_delete=models.CASCADE, null=True, blank=True,
        related_name="community_transaction_fees",
        help_text="Null = blanket fee for all assets in this community.",
    )
    fee_bps = models.IntegerField(
        help_text="Fee in basis points (100 = 1%). Negative allowed for per-asset adjustments.",
    )
    set_by = models.CharField(max_length=10, choices=SET_BY_CHOICES, default=SET_BY_ADMIN)
    decision = models.ForeignKey(
        AssemblyDecision, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="enacted_fees",
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["community", "asset"]
        indexes = [
            models.Index(fields=["community", "active"]),
            models.Index(fields=["asset"]),
        ]

    def __str__(self):
        asset_label = self.asset.unit_name if self.asset else "all assets"
        return f"{self.community} – {asset_label} {self.fee_bps}bps ({self.set_by})"


class PollTax(models.Model):
    """
    Community poll tax. Tax amount formula (in payment asset base units):
        total = head_amount_base_units + floor(member_wealth_base_units * wealth_rate_bps / 10000)

    Where member_wealth_base_units = member's balance of wealth_asset (0 if wealth_asset is null).
    federal_agent members are always exempt.
    """
    community = models.ForeignKey(
        "socialhub.Community", on_delete=models.CASCADE, related_name="poll_taxes",
    )
    payment_asset = models.ForeignKey(
        "assets.Asset", on_delete=models.PROTECT, related_name="poll_taxes_payment",
        help_text="Asset in which the tax is collected.",
    )
    head_amount_base_units = models.BigIntegerField(
        default=0,
        help_text="Flat per-member amount (head tax component).",
    )
    wealth_rate_bps = models.PositiveIntegerField(
        default=0,
        help_text="Rate in basis points applied to member's wealth_asset balance.",
    )
    wealth_asset = models.ForeignKey(
        "assets.Asset", on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="poll_taxes_wealth",
        help_text="Asset used to measure member wealth. Null = no wealth component.",
    )
    period = models.CharField(max_length=20, choices=PollTaxPeriod.choices, default=PollTaxPeriod.YEARLY)
    active = models.BooleanField(default=True)
    decision = models.ForeignKey(
        AssemblyDecision, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="enacted_poll_taxes",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["community"]
        indexes = [models.Index(fields=["community", "active"])]

    def __str__(self):
        return f"{self.community} poll tax – head:{self.head_amount_base_units} wealth_rate:{self.wealth_rate_bps}bps / {self.period}"

    def current_period_label(self) -> str:
        now = timezone.now()
        if self.period == PollTaxPeriod.MONTHLY:
            return now.strftime("%Y-%m")
        if self.period == PollTaxPeriod.QUARTERLY:
            q = (now.month - 1) // 3 + 1
            return f"{now.year}-Q{q}"
        if self.period == PollTaxPeriod.YEARLY:
            return str(now.year)
        return "one_time"

    def compute_amount_for(self, person) -> int:
        """
        Compute total tax owed (in payment_asset base units) for a given person.
        Returns 0 if person is a committed citizen or member of a federal tribe community.
        """
        from toto.people.civic import is_committed_citizen
        if is_committed_citizen(person):
            return 0
        if person.communities.filter(is_federal_tribe=True).exists():
            return 0

        total = self.head_amount_base_units

        if self.wealth_rate_bps and self.wealth_asset:
            wealth_balance = self._get_member_wealth(person)
            total += (wealth_balance * self.wealth_rate_bps) // 10000

        return max(0, total)

    def _get_member_wealth(self, person) -> int:
        """Return person's total balance of wealth_asset in base units across all their accounts."""
        try:
            from toto.assets.models import LedgerAccount
            # Sum all ledger accounts linked to this person's user
            if person.user_id:
                accounts = LedgerAccount.objects.filter(owner=person.user)
                total = 0
                for account in accounts:
                    # Use balance property if it exists, otherwise default to 0
                    balance = getattr(account, "balance_base_units", None)
                    if balance is None:
                        balance = getattr(account, "balance", 0)
                    total += int(balance or 0)
                return total
        except Exception:
            pass
        return 0


class PollTaxPayment(models.Model):
    poll_tax = models.ForeignKey(PollTax, on_delete=models.CASCADE, related_name="payments")
    person = models.ForeignKey(
        "people.Person", on_delete=models.CASCADE, related_name="poll_tax_payments",
    )
    period_label = models.CharField(max_length=20)
    head_amount_paid_base_units = models.BigIntegerField(default=0)
    wealth_amount_paid_base_units = models.BigIntegerField(default=0)
    total_paid_base_units = models.BigIntegerField(default=0)
    paid_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("poll_tax", "person", "period_label")]
        ordering = ["-paid_at"]
        indexes = [
            models.Index(fields=["poll_tax", "person"]),
            models.Index(fields=["person", "period_label"]),
        ]

    def __str__(self):
        return f"{self.person} – {self.poll_tax.community} {self.period_label} ({self.total_paid_base_units})"


class CommunitySenate(models.Model):
    """
    Senate settings for a bicameral community assembly.
    Senators are the community's senior_members — no separate membership list.
    The senate's only power is to veto proposals that passed the popular assembly,
    within the configured veto window.
    Senators may also vote in the popular assembly.
    Enabled only when CommunityAssemblyConfig.is_bicameral is True.
    """
    community = models.OneToOneField(
        "socialhub.Community",
        on_delete=models.CASCADE,
        related_name="senate",
    )
    is_active = models.BooleanField(default=True)
    veto_window_days = models.PositiveIntegerField(
        default=7,
        help_text="Days the senate has to veto a proposal after it passes the popular assembly.",
    )
    members = models.ManyToManyField(
        "people.Person",
        blank=True,
        related_name="senate_memberships",
        help_text="Legacy explicit senators. Senators are now derived from community.senior_members.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Community senate"
        verbose_name_plural = "Community senates"

    def __str__(self):
        status = "active" if self.is_active else "inactive"
        return f"Senate of {self.community} ({status}, {self.veto_window_days}d window)"


class SenateVeto(models.Model):
    """
    A veto cast by a senator (senior member) against a proposal that passed
    the popular assembly and is awaiting senate review.
    One veto is sufficient to block enactment. The first senator to veto wins.
    """
    proposal = models.OneToOneField(
        AssemblyProposal,
        on_delete=models.CASCADE,
        related_name="senate_veto",
    )
    senator = models.ForeignKey(
        "people.Person",
        on_delete=models.CASCADE,
        related_name="senate_vetoes",
    )
    reason = models.TextField(
        blank=True,
        help_text="Public explanation of why the senate is blocking this proposal.",
    )
    vetoed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Senate veto"
        verbose_name_plural = "Senate vetoes"

    def __str__(self):
        return f"Veto of '{self.proposal.title}' by {self.senator}"
