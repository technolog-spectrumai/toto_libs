from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


def _unique_slug(instance, value):
    base = slugify(value) or instance.__class__.__name__.lower()
    slug = base
    n = 1
    qs = instance.__class__.objects.exclude(pk=instance.pk)
    while qs.filter(slug=slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


class MagistrateRole(models.Model):
    """
    Office definition — e.g. Prefect, Tribune, Legate, Quaestor.
    Admin-managed; seeded by ingress_magistrate.
    """
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=100, default="fa-solid fa-scroll")

    # Oversight domains
    overseeing_tribunal = models.BooleanField(
        default=False,
        help_text="Holder oversees tribunal proceedings.",
    )
    overseeing_trade = models.BooleanField(
        default=False,
        help_text="Holder oversees trade and commerce.",
    )
    overseeing_finance = models.BooleanField(
        default=False,
        help_text="Holder oversees community finances and taxation.",
    )
    overseeing_public_order = models.BooleanField(
        default=False,
        help_text="Holder oversees public order and safety.",
    )
    overseeing_merchandise = models.BooleanField(
        default=False,
        help_text="Holder may inspect merchandise quality and impose fines. Fines may be contested before the tribunal.",
    )
    overseeing_education = models.BooleanField(
        default=False,
        help_text="Holder oversees community education, academies, and knowledge standards.",
    )
    overseeing_relations = models.BooleanField(
        default=False,
        help_text="Holder oversees inter-community diplomatic relations, treaties, and external liaisons.",
    )
    overseeing_logistics = models.BooleanField(
        default=False,
        help_text="Holder oversees logistics, supply chains, and transport operations.",
    )
    overseeing_interior = models.BooleanField(
        default=False,
        help_text="Holder oversees internal travel, location access, and movement of persons within community territory.",
    )
    overseeing_productivity = models.BooleanField(
        default=False,
        help_text="Holder oversees community productivity, work assignments, and labour standards.",
    )
    can_set_fines = models.BooleanField(
        default=False,
        help_text="Holder may levy infraction fines on community members within their oversight domains. Bounded by the community max-fine-pct setting.",
    )
    can_freeze_assets = models.BooleanField(
        default=False,
        help_text="Holder may issue a temporary freeze on an asset, preventing all transfers of that token until the freeze is lifted.",
    )

    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "name"]
        verbose_name = "Magistrate role"
        verbose_name_plural = "Magistrate roles"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(self, self.name)
        super().save(*args, **kwargs)

    @property
    def oversight_domains(self) -> list[str]:
        mapping = [
            ("overseeing_tribunal", "Tribunal"),
            ("overseeing_trade", "Trade"),
            ("overseeing_finance", "Finance"),
            ("overseeing_public_order", "Public Order"),
            ("overseeing_merchandise", "Merchandise Quality"),
            ("overseeing_education", "Education"),
            ("overseeing_relations", "Inter-Community Relations"),
            ("overseeing_logistics", "Logistics"),
            ("overseeing_interior", "Interior & Movement"),
            ("overseeing_productivity", "Productivity & Work"),
            ("can_freeze_assets", "Asset Freeze"),
        ]
        return [label for field, label in mapping if getattr(self, field)]


class Magistrate(models.Model):
    STATUS_CHOICES = [
        ("active", "Active"),
        ("suspended", "Suspended"),
        ("impeached", "Impeached"),
        ("term_ended", "Term Ended"),
    ]

    person = models.ForeignKey(
        "people.Person",
        on_delete=models.CASCADE,
        related_name="magistrate_seats",
    )
    role = models.ForeignKey(
        MagistrateRole,
        on_delete=models.CASCADE,
        related_name="holders",
    )
    community = models.ForeignKey(
        "socialhub.Community",
        on_delete=models.CASCADE,
        related_name="magistrates",
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active", db_index=True)

    term_start = models.DateField()
    term_end = models.DateField(null=True, blank=True)

    elected_at = models.DateTimeField(default=timezone.now)
    source_proposal = models.ForeignKey(
        "assembly.AssemblyProposal",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="elected_magistrates",
    )

    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # One active seat per person per role per community; historical seats allowed after term_ended
        ordering = ["-elected_at"]
        verbose_name = "Magistrate"
        verbose_name_plural = "Magistrates"
        indexes = [
            models.Index(fields=["community", "status"]),
            models.Index(fields=["role", "status"]),
        ]

    def __str__(self):
        return f"{self.person} — {self.role} ({self.community})"

    def clean(self):
        if self.person_id:
            from toto.people.models import Person
            p = Person.objects.get(pk=self.person_id)
            if not p.is_federal_agent:
                raise ValidationError("Only federal agents may hold magistrate roles.")

    @property
    def is_active(self) -> bool:
        if self.status != "active":
            return False
        if self.term_end and timezone.now().date() > self.term_end:
            return False
        return True


class MagistrateDecision(models.Model):
    """
    Fast executive decision issued by an active magistrate.
    Enters the community decision ledger immediately and is subject to assembly review.
    """
    DECISION_TYPES = [
        ("emergency_declare",      "Emergency Declaration"),
        ("tribunal_order",         "Tribunal Order"),
        ("trade_order",            "Trade Order"),
        ("finance_directive",      "Finance Directive"),
        ("public_order_directive", "Public Order Directive"),
        ("trade_reversal",         "Trade Reversal Order"),
        ("merchandise_fine",       "Merchandise Quality Fine"),
        ("education_directive",    "Education Directive"),
        ("relations_directive",    "Relations Directive"),
        ("logistics_order",        "Logistics Order"),
        ("interior_directive",     "Interior Directive"),
        ("productivity_directive", "Productivity Directive"),
        ("infraction_fine",        "Infraction Fine"),
        ("asset_freeze",           "Asset Freeze Order"),
        ("asset_freeze_lift",      "Asset Freeze Lift"),
        ("general",                "General Directive"),
    ]
    STATUS_CHOICES = [
        ("active",   "Active"),
        ("revoked",  "Revoked"),
        ("reviewed", "Reviewed by Assembly"),
    ]

    magistrate = models.ForeignKey(
        Magistrate, on_delete=models.CASCADE, related_name="decisions",
    )
    community = models.ForeignKey(
        "socialhub.Community", on_delete=models.CASCADE, related_name="magistrate_decisions",
    )
    decision_type = models.CharField(max_length=30, choices=DECISION_TYPES, db_index=True)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active", db_index=True)
    reviewed_by = models.ForeignKey(
        "people.Person", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="reviewed_magistrate_decisions",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Magistrate decision"
        verbose_name_plural = "Magistrate decisions"
        indexes = [
            models.Index(fields=["community", "status"]),
        ]

    def __str__(self):
        return self.title

    @property
    def type_label(self) -> str:
        return dict(self.DECISION_TYPES).get(self.decision_type, self.decision_type)


class MagistrateReport(models.Model):
    """
    Periodic report submitted by a magistrate to the assembly.
    Acknowledged by an assembly member — creates an auditable record.
    """
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("submitted", "Submitted"),
        ("acknowledged", "Acknowledged"),
    ]

    magistrate = models.ForeignKey(
        Magistrate,
        on_delete=models.CASCADE,
        related_name="reports",
    )
    title = models.CharField(max_length=255)
    body = models.TextField()

    reporting_period_start = models.DateField(null=True, blank=True)
    reporting_period_end = models.DateField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft", db_index=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    acknowledged_by = models.ForeignKey(
        "people.Person",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="acknowledged_magistrate_reports",
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Magistrate report"
        verbose_name_plural = "Magistrate reports"

    def __str__(self):
        return self.title


class CommunityMagistrateSettings(models.Model):
    """
    Per-community configuration for the magistrate system.
    Created by admin when the magistrate app is activated for a community.
    """
    community = models.OneToOneField(
        "socialhub.Community",
        on_delete=models.CASCADE,
        related_name="magistrate_settings",
    )
    max_fine_pct = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("10.00"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text="Maximum infraction fine a magistrate may levy, expressed as a percentage of the target's assessed holdings (0–100).",
    )
    fine_collection_account = models.ForeignKey(
        "assets.LedgerAccount",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="magistrate_fine_collections",
        help_text="Ledger account that receives collected fines. If unset, fines are recorded but the transfer must be processed manually.",
    )

    class Meta:
        verbose_name = "Community magistrate settings"
        verbose_name_plural = "Community magistrate settings"

    def __str__(self):
        return f"Magistrate settings — {self.community} (max fine: {self.max_fine_pct}%)"


class MagistrateFine(models.Model):
    """
    Infraction fine levied by a magistrate against a community member.
    Linked to the public decision ledger. May be contested before the tribunal.
    """
    STATUS_CHOICES = [
        ("issued",              "Issued — Obligation Pending"),
        ("obligation_created",  "Obligation Created"),
        ("pending_collection",  "Pending — No Collection Account"),
        ("contested",           "Contested at Tribunal"),
        ("overturned",          "Overturned"),
    ]

    decision = models.OneToOneField(
        MagistrateDecision,
        on_delete=models.CASCADE,
        related_name="fine_detail",
    )
    target_person = models.ForeignKey(
        "people.Person",
        on_delete=models.CASCADE,
        related_name="magistrate_fines_received",
    )
    target_account = models.ForeignKey(
        "assets.LedgerAccount",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="magistrate_fines_debited",
    )
    asset = models.ForeignKey(
        "assets.Asset",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="magistrate_fines",
    )
    authority_domain = models.CharField(
        max_length=60,
        blank=True,
        help_text="The magistrate oversight domain under which this fine was issued (e.g. 'Finance', 'Trade').",
    )
    fine_pct = models.DecimalField(
        max_digits=5, decimal_places=2,
        help_text="Percentage of assessed holdings applied as a fine.",
    )
    basis_amount_display = models.DecimalField(
        max_digits=24, decimal_places=8, default=Decimal("0"),
        help_text="Assessed holding balance at the time of the fine.",
    )
    fine_amount_display = models.DecimalField(
        max_digits=24, decimal_places=8, default=Decimal("0"),
        help_text="Actual fine amount (fine_pct % of basis).",
    )
    infraction = models.TextField(help_text="Description of the infraction.")
    obligation = models.ForeignKey(
        "assets.Obligation",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="magistrate_fine",
        help_text="The asset obligation created to enforce this fine (mirrors tribunal fine pattern).",
    )
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="issued", db_index=True)
    contested_at = models.DateTimeField(null=True, blank=True)
    overturned_at = models.DateTimeField(null=True, blank=True)
    overturned_by = models.ForeignKey(
        "people.Person",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="overturned_magistrate_fines",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Magistrate fine"
        verbose_name_plural = "Magistrate fines"

    def __str__(self):
        return f"Fine on {self.target_person} ({self.fine_pct}%) — {self.decision.community}"


class AssetFreeze(models.Model):
    """
    Magistrate-issued freeze on an asset token.
    While an active freeze exists, transfer_asset raises ValidationError for that asset.
    """
    STATUS_ACTIVE = "active"
    STATUS_LIFTED = "lifted"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_LIFTED, "Lifted"),
    ]

    asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.CASCADE,
        related_name="magistrate_freezes",
    )
    decision = models.OneToOneField(
        MagistrateDecision,
        on_delete=models.CASCADE,
        related_name="asset_freeze_detail",
    )
    reason = models.TextField(help_text="Grounds for the freeze order.")
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
        db_index=True,
    )
    lifted_at = models.DateTimeField(null=True, blank=True)
    lift_decision = models.OneToOneField(
        MagistrateDecision,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="asset_freeze_lift_detail",
    )
    lift_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Asset freeze"
        verbose_name_plural = "Asset freezes"

    def __str__(self):
        return f"Freeze on {self.asset.unit_name} ({self.status}) — {self.decision.community}"

    @property
    def is_active(self) -> bool:
        return self.status == self.STATUS_ACTIVE
