from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from toto.core.domain import DomainEntity

User = get_user_model()


class PractitionerIncomeProfile(models.Model):
    """
    Holds the optional default income account for a Practitioner.
    Kept here (not on kanban.Practitioner) so kanban has no FK into assets.
    """

    practitioner = models.OneToOneField(
        "kanban.Practitioner",
        on_delete=models.CASCADE,
        related_name="income_profile",
    )
    default_income_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="practitioner_income_profiles",
        help_text="Default account receiving salary, vesting releases, bonuses, or lease payments.",
    )

    class Meta:
        verbose_name = "Practitioner Income Profile"
        verbose_name_plural = "Practitioner Income Profiles"

    def __str__(self):
        return f"Income profile — {self.practitioner}"


class PractitionerAllowance(DomainEntity):
    ALLOWANCE_TYPE_CHOICES = [
        ("per_diem", "Per Diem"),
        ("hourly", "Hourly"),
        ("fixed", "Fixed"),
        ("travel", "Travel"),
        ("meal", "Meal"),
        ("other", "Other"),
    ]

    practitioner = models.ForeignKey(
        "kanban.Practitioner",
        on_delete=models.CASCADE,
        related_name="allowances",
    )
    payer_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="mission_economy_allowances_to_pay",
        help_text="Account that pays this allowance. Defaults to the project owner's wallet.",
    )
    recipient_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="mission_economy_allowances_to_receive",
        help_text="Account that receives the allowance. Resolved from practitioner if blank.",
    )
    asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="mission_economy_practitioner_allowances",
    )
    amount_base_units = models.BigIntegerField(
        help_text="Amount in the asset's smallest unit (no decimals, no floats).",
    )
    allowance_type = models.CharField(max_length=20, choices=ALLOWANCE_TYPE_CHOICES, default="fixed")
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)
    metadata = models.JSONField(blank=True, null=True)

    def __str__(self):
        return f"{self.allowance_type} allowance for {self.practitioner} ({self.amount_base_units} {self.asset})"

    @property
    def amount_display(self):
        from toto.assets.models import from_base_units
        return from_base_units(self.amount_base_units, self.asset.decimals)

    @classmethod
    def default_payer_account_for(cls, project):
        """Return the project owner's primary LedgerAccount, or None."""
        try:
            from toto.assets.models import LedgerAccount
            owner = project.project_lead
            if owner and owner.user_id:
                return LedgerAccount.objects.filter(owner=owner.user, active=True).order_by("pk").first()
        except Exception:
            pass
        return None

    def clean(self):
        if self.amount_base_units is not None and self.amount_base_units <= 0:
            raise ValidationError({"amount_base_units": "Amount must be greater than zero."})

        if self.asset_id:
            try:
                from toto.assets.models import Asset
                asset = Asset.objects.get(pk=self.asset_id)
                if not asset.active:
                    raise ValidationError({"asset": "Asset must be active."})
            except Asset.DoesNotExist:
                pass

        if self.payer_account_id:
            try:
                from toto.assets.models import LedgerAccount
                acct = LedgerAccount.objects.get(pk=self.payer_account_id)
                if not acct.active:
                    raise ValidationError({"payer_account": "Payer account must be active."})
            except LedgerAccount.DoesNotExist:
                pass

        if self.recipient_account_id:
            try:
                from toto.assets.models import LedgerAccount
                acct = LedgerAccount.objects.get(pk=self.recipient_account_id)
                if not acct.active:
                    raise ValidationError({"recipient_account": "Recipient account must be active."})
            except LedgerAccount.DoesNotExist:
                pass

        if self.payer_account_id and self.recipient_account_id and self.payer_account_id == self.recipient_account_id:
            raise ValidationError("Payer and recipient accounts must differ.")

        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValidationError({"valid_until": "valid_until must be on or after valid_from."})

    def to_obligation_kwargs(self):
        """Return kwargs suitable for assets.services.assets.create_obligation."""
        return {
            "debtor_account": self.payer_account,
            "creditor_account": self.recipient_account,
            "asset": self.asset,
            "amount_base_units": self.amount_base_units,
        }


class ProjectTokenizationStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    DEFAULTED = "defaulted", "Defaulted"


class ProjectTokenizationDefaultReason(models.TextChoices):
    INSOLVENCY = "insolvency", "Insolvency"
    ABANDONMENT = "abandonment", "Abandonment"
    FRAUD = "fraud", "Fraud"
    TECHNICAL = "technical", "Technical Failure"
    OTHER = "other", "Other"


class ProjectTokenizationQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status=ProjectTokenizationStatus.ACTIVE)

    def defaulted(self):
        return self.filter(status=ProjectTokenizationStatus.DEFAULTED)


class ProjectTokenization(models.Model):
    objects = ProjectTokenizationQuerySet.as_manager()

    project = models.OneToOneField(
        "kanban.Project",
        on_delete=models.PROTECT,
        related_name="tokenization",
    )
    asset = models.OneToOneField(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="project_tokenization",
    )
    supervisor = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="supervised_project_tokenizations",
    )
    status = models.CharField(
        max_length=20,
        choices=ProjectTokenizationStatus.choices,
        default=ProjectTokenizationStatus.ACTIVE,
    )
    default_reason = models.CharField(
        max_length=40,
        choices=ProjectTokenizationDefaultReason.choices,
        blank=True,
    )
    default_note = models.TextField(blank=True)
    defaulted_at = models.DateTimeField(null=True, blank=True)
    defaulted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="defaulted_project_tokenizations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"], name="meco_proj_status_idx"),
            models.Index(fields=["created_at"], name="meco_proj_created_idx"),
        ]

    def __str__(self):
        return f"{self.project} tokenized as {self.asset.unit_name}"

    def delete(self, *args, **kwargs):
        raise ValidationError("Project tokenization records are permanent and cannot be deleted.")

    @property
    def is_defaulted(self) -> bool:
        return self.status == ProjectTokenizationStatus.DEFAULTED
