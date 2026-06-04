from django.db import models
from django.utils.text import slugify
from toto.core.domain import DomainEntity


def unique_slug_for(instance, field_value, *, scope=None, slug_field="slug"):
    base = slugify(field_value) or instance.__class__.__name__.lower()
    slug = base
    counter = 1
    qs = instance.__class__.objects.all()
    if scope:
        qs = qs.filter(**scope)
    while qs.filter(**{slug_field: slug}).exclude(pk=instance.pk).exists():
        counter += 1
        slug = f"{base}-{counter}"
    return slug


class TribunalCase(DomainEntity):
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=280, unique=True, blank=True)
    case_number = models.CharField(max_length=64, unique=True, blank=True)

    reason = models.CharField(
        max_length=255,
        blank=True,
        help_text="Short reason for opening the case.",
    )

    description = models.TextField(blank=True)

    opened_by = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="opened_tribunal_cases",
    )

    assigned_to = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tribunal_cases",
        help_text="Mediator, judge, supervisor, or responsible reviewer.",
    )

    related_object = models.ForeignKey(
        "inventory.RealWorldObject",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tribunal_cases",
    )

    related_order = models.ForeignKey(
        "bazaar.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tribunal_cases",
        help_text="Bazaar order this dispute concerns.",
    )

    status = models.CharField(
        max_length=64,
        default="open",
        blank=True,
        help_text="open, under_review, awaiting_evidence, resolved, dismissed, escalated, etc.",
    )

    priority = models.CharField(
        max_length=64,
        default="normal",
        blank=True,
        help_text="low, normal, high, urgent, etc.",
    )

    opened_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-opened_at"]
        indexes = [
            models.Index(fields=["case_number"]),
            models.Index(fields=["slug"]),
            models.Index(fields=["status"]),
            models.Index(fields=["priority"]),
            models.Index(fields=["opened_by"]),
            models.Index(fields=["assigned_to"]),
            models.Index(fields=["related_object"]),
            models.Index(fields=["related_order"]),
        ]

    def __str__(self):
        return self.case_number or self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug_for(self, self.title)

        super().save(*args, **kwargs)

        if not self.case_number and self.opened_at and self.pk:
            self.case_number = f"TRB-{self.opened_at.strftime('%Y%m%d')}-{self.pk}"
            super().save(update_fields=["case_number"])


class TribunalParty(DomainEntity):
    case = models.ForeignKey(
        TribunalCase,
        on_delete=models.CASCADE,
        related_name="parties",
    )

    person = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tribunal_participations",
    )

    role = models.CharField(
        max_length=64,
        blank=True,
        help_text="claimant, respondent, witness, mediator, custodian, verifier, observer, etc.",
    )

    statement = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["case", "role", "created_at"]
        indexes = [
            models.Index(fields=["case"]),
            models.Index(fields=["person"]),
            models.Index(fields=["role"]),
        ]

    def __str__(self):
        return f"{self.person or 'Unknown party'} — {self.role or 'party'}"


class TribunalClaim(DomainEntity):
    case = models.ForeignKey(
        TribunalCase,
        on_delete=models.CASCADE,
        related_name="claims",
    )

    claimant = models.ForeignKey(
        TribunalParty,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="claims_made",
    )

    claim_type = models.CharField(
        max_length=64,
        blank=True,
        help_text="ownership, custody, damage, missing_object, fraud, valuation, breach, verification, etc.",
    )

    summary = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    requested_resolution = models.TextField(
        blank=True,
        help_text="What the claimant wants: return, compensation, transfer, correction, dismissal, etc.",
    )

    amount = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True)

    currency = models.ForeignKey(
        "assets.Currency",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tribunal_claims",
    )

    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["case"]),
            models.Index(fields=["claim_type"]),
            models.Index(fields=["claimant"]),
            models.Index(fields=["currency"]),
        ]

    def __str__(self):
        return self.summary


class TribunalEvidence(DomainEntity):
    case = models.ForeignKey(
        TribunalCase,
        on_delete=models.CASCADE,
        related_name="evidence",
    )

    submitted_by = models.ForeignKey(
        TribunalParty,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_evidence",
    )

    evidence_type = models.CharField(
        max_length=64,
        blank=True,
        help_text="document, photo, statement, ledger_reference, object_snapshot, external_link, file_hash, etc.",
    )

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    file = models.FileField(upload_to="tribunal/evidence/", null=True, blank=True)
    file_hash = models.CharField(max_length=64, blank=True)
    external_reference = models.CharField(max_length=255, blank=True)
    external_url = models.URLField(blank=True)

    related_object = models.ForeignKey(
        "inventory.RealWorldObject",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tribunal_evidence",
    )

    metadata = models.JSONField(default=dict, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-submitted_at"]
        indexes = [
            models.Index(fields=["case"]),
            models.Index(fields=["submitted_by"]),
            models.Index(fields=["evidence_type"]),
            models.Index(fields=["related_object"]),
            models.Index(fields=["file_hash"]),
        ]

    def __str__(self):
        return self.title


class JurySession(DomainEntity):
    STATUS_CHOICES = [
        ('open', 'Open'),
        ('closed', 'Closed'),
        ('cancelled', 'Cancelled'),
    ]
    OUTCOME_CHOICES = [
        ('pending', 'Pending'),
        ('guilty', 'Guilty'),
        ('not_guilty', 'Not Guilty'),
        ('hung', 'Hung Jury'),
        ('cancelled', 'Cancelled'),
    ]

    case = models.ForeignKey(
        TribunalCase,
        on_delete=models.CASCADE,
        related_name='jury_sessions',
    )
    created_by = models.ForeignKey(
        'people.Person',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_jury_sessions',
    )
    jurors = models.ManyToManyField(
        'people.Person',
        blank=True,
        related_name='jury_session_invites',
        help_text='People invited to vote. Leave empty to allow any member to vote.',
    )
    opens_at = models.DateTimeField()
    closes_at = models.DateTimeField()
    required_votes = models.PositiveIntegerField(
        default=0,
        help_text='Minimum votes needed for a valid outcome (0 = no minimum).',
    )
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='open')
    outcome = models.CharField(max_length=32, choices=OUTCOME_CHOICES, default='pending', blank=True)
    note = models.TextField(blank=True, help_text='Instructions or context for jurors.')
    created_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['case']),
            models.Index(fields=['status']),
            models.Index(fields=['closes_at']),
        ]

    def __str__(self):
        return f'Jury session #{self.pk} for {self.case} ({self.status})'

    @property
    def is_open(self):
        from django.utils import timezone
        return self.status == 'open' and timezone.now() < self.closes_at

    def tally(self):
        counts = {'guilty': 0, 'not_guilty': 0, 'abstain': 0}
        for v in self.votes.values_list('vote', flat=True):
            counts[v] = counts.get(v, 0) + 1
        return counts

    def compute_outcome(self):
        counts = self.tally()
        g, ng = counts['guilty'], counts['not_guilty']
        if g == 0 and ng == 0:
            return 'pending'
        if g > ng:
            return 'guilty'
        if ng > g:
            return 'not_guilty'
        return 'hung'


class JuryVote(DomainEntity):
    VOTE_CHOICES = [
        ('guilty', 'Guilty'),
        ('not_guilty', 'Not Guilty'),
        ('abstain', 'Abstain'),
    ]

    session = models.ForeignKey(
        JurySession,
        on_delete=models.CASCADE,
        related_name='votes',
    )
    juror = models.ForeignKey(
        'people.Person',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='jury_votes',
    )
    vote = models.CharField(max_length=16, choices=VOTE_CHOICES)
    reason = models.TextField(blank=True)
    voted_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['voted_at']
        unique_together = [('session', 'juror')]
        indexes = [
            models.Index(fields=['session']),
            models.Index(fields=['juror']),
            models.Index(fields=['vote']),
        ]

    def __str__(self):
        return f'{self.juror} → {self.vote}'


class TribunalRuling(DomainEntity):
    case = models.ForeignKey(
        TribunalCase,
        on_delete=models.CASCADE,
        related_name="rulings",
    )

    decided_by = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tribunal_rulings",
    )

    title = models.CharField(max_length=255)

    decision = models.TextField(help_text="The actual ruling / decision.")
    reasoning = models.TextField(blank=True, help_text="Why this decision was made.")

    resolution_type = models.CharField(
        max_length=64,
        blank=True,
        help_text="dismissed, upheld, partial, settlement, compensation, transfer_required, correction_required, etc.",
    )

    amount_awarded = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True)

    currency = models.ForeignKey(
        "assets.Currency",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tribunal_rulings",
    )

    fine_debtor_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tribunal_fines_due",
        help_text="Account that must pay the fine.",
    )

    fine_creditor_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tribunal_fines_receivable",
        help_text="Account that receives the fine.",
    )

    fine_asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tribunal_fines",
        help_text="Asset used to pay the fine.",
    )

    fine_amount = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True)
    fine_due_at = models.DateTimeField(null=True, blank=True)

    fine_obligation = models.ForeignKey(
        "assets.Obligation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tribunal_rulings",
        help_text="Asset obligation created for this fine.",
    )

    effective_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["case"]),
            models.Index(fields=["decided_by"]),
            models.Index(fields=["resolution_type"]),
            models.Index(fields=["currency"]),
            models.Index(fields=["fine_debtor_account"]),
            models.Index(fields=["fine_creditor_account"]),
            models.Index(fields=["fine_asset"]),
            models.Index(fields=["fine_obligation"]),
        ]

    def __str__(self):
        return self.title
