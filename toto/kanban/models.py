from django.db import models
from django.core.exceptions import ValidationError
from toto.core.domain import DomainEntity
from toto.people.models import Person
from toto.verbena.models import AbstractPage, AbstractSection


THREE_SCALE = [
    (1, "Low"),
    (2, "Medium"),
    (3, "High"),
]

FIB_SCALE = [
    (1, "Tiny"),
    (2, "Small"),
    (3, "Medium"),
    (5, "Big"),
    (8, "Large"),
]


class Project(DomainEntity):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    project_lead = models.ForeignKey(Person, on_delete=models.CASCADE)

    def __str__(self):
        return self.name


class Column(DomainEntity):
    graph_node_type = "TaskStatus"
    project = models.ForeignKey(Project, on_delete=models.CASCADE, db_column="belongs_to_project")
    name = models.CharField(max_length=100)
    position = models.PositiveIntegerField()
    can_add_task = models.BooleanField(default=False)
    auditors = models.ManyToManyField(
        "Practitioner",
        related_name="audited_columns",
        blank=True,
        help_text="Practitioners who can move tasks into this column.",
    )

    def __str__(self):
        return self.name


class Campaign(DomainEntity):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="campaigns")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    owner = models.ForeignKey(Person, on_delete=models.SET_NULL, null=True, blank=True)
    metadata = models.JSONField(blank=True, null=True)
    zone = models.ForeignKey(
        "locations.Zone",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="campaigns",
    )

    def __str__(self):
        return self.name


class Mission(DomainEntity):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="missions")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    urgency = models.IntegerField(choices=THREE_SCALE, default=2)
    impact = models.IntegerField(choices=THREE_SCALE, default=2)
    location = models.ForeignKey(
        "locations.Address",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="missions",
    )
    route = models.ForeignKey(
        "locations.Route",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="missions",
    )
    owner = models.ForeignKey(Person, on_delete=models.SET_NULL, null=True, blank=True)
    metadata = models.JSONField(blank=True, null=True)

    def __str__(self):
        return f"{self.title} ({self.campaign.name})"

    @property
    def urgency_label(self):
        return dict(THREE_SCALE).get(self.urgency, self.urgency)

    @property
    def impact_label(self):
        return dict(THREE_SCALE).get(self.impact, self.impact)

    @property
    def effective_zone(self):
        return self.campaign.zone


class Sprint(DomainEntity):
    name = models.CharField(max_length=100)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()

    def __str__(self):
        return self.name


class Practitioner(DomainEntity):
    """A professional profile for a Person, independent of any specific project."""

    ROLE_CONTRIBUTOR = "contributor"
    ROLE_REVIEWER = "reviewer"
    ROLE_AUDITOR = "auditor"
    ROLE_MANAGER = "manager"
    ROLE_OBSERVER = "observer"

    ROLE_CHOICES = [
        (ROLE_CONTRIBUTOR, "Contributor"),
        (ROLE_REVIEWER, "Reviewer"),
        (ROLE_AUDITOR, "Auditor"),
        (ROLE_MANAGER, "Manager"),
        (ROLE_OBSERVER, "Observer"),
    ]

    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="practitioner_profiles")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_CONTRIBUTOR)
    is_active = models.BooleanField(default=True)
    default_income_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="practitioner_income_accounts",
        help_text="Default account receiving salary, vesting releases, bonuses, revenue-share payouts, or lease/subscription payments.",
    )
    work_description = models.TextField(
        blank=True,
        help_text="Free-text description of this practitioner's role or services in the project.",
    )
    metadata = models.JSONField(blank=True, null=True)

    def __str__(self):
        return f"{self.person} ({self.role})"


class ProjectCommitment(models.Model):
    """Links a Practitioner to a Project and tracks their time commitment."""

    practitioner = models.ForeignKey(Practitioner, on_delete=models.CASCADE, related_name="commitments")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="commitments")
    hours_per_day = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        help_text="Number of hours per day committed to this project.",
    )
    is_active = models.BooleanField(default=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    metadata = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["practitioner", "project"], name="unique_commitment_per_project"),
        ]

    def __str__(self):
        return f"{self.practitioner} → {self.project} ({self.hours_per_day}h/day)"


class Task(DomainEntity):
    mission = models.ForeignKey(Mission, on_delete=models.CASCADE, related_name="tasks")
    column = models.ForeignKey(Column, on_delete=models.CASCADE, related_name="tasks")
    sprint = models.ForeignKey(Sprint, on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    assignee = models.ForeignKey(
        Practitioner,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tasks",
    )
    reviewer = models.ForeignKey(
        Practitioner,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="review_tasks",
        help_text="Optional reviewer who signs off the task before it is completed.",
    )
    due_date = models.DateField(null=True, blank=True)
    position = models.PositiveIntegerField(default=0)
    weight = models.IntegerField(choices=FIB_SCALE, default=1)
    metadata = models.JSONField(blank=True, null=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.title

    @property
    def weight_label(self):
        return dict(FIB_SCALE).get(self.weight, self.weight)

    def clean(self):
        if not self.mission_id:
            return
        try:
            project = self.mission.campaign.project
        except (Mission.DoesNotExist, Campaign.DoesNotExist, Project.DoesNotExist):
            return

        if self.column_id and self.column.project_id != project.pk:
            raise ValidationError({"column": "Column must belong to the same project as the task."})

        if self.sprint_id and self.sprint.project_id != project.pk:
            raise ValidationError({"sprint": "Sprint must belong to the same project as the task."})



class PractitionerAllowance(DomainEntity):
    ALLOWANCE_TYPE_CHOICES = [
        ("per_diem", "Per Diem"),
        ("hourly", "Hourly"),
        ("fixed", "Fixed"),
        ("travel", "Travel"),
        ("meal", "Meal"),
        ("other", "Other"),
    ]

    practitioner = models.ForeignKey(Practitioner, on_delete=models.CASCADE, related_name="allowances")
    payer_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="kanban_allowances_to_pay",
        help_text="Account that pays this allowance. Defaults to the project owner's wallet.",
    )
    recipient_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="kanban_allowances_to_receive",
        help_text="Account that receives the allowance. Resolved from practitioner if blank.",
    )
    asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="kanban_practitioner_allowances",
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


class DocumentationPage(AbstractPage):
    mission = models.OneToOneField(
        Mission,
        on_delete=models.CASCADE,
        related_name="documentation_page",
    )
    is_manual = models.BooleanField(
        default=False,
        help_text="If true, this page is an instruction / how-to manual for the mission.",
    )

    class Meta:
        verbose_name = "Documentation Page"
        verbose_name_plural = "Documentation Pages"

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse("kanban:documentation_page_detail", args=[self.pk])


class DocumentationSection(AbstractSection):
    page = models.ForeignKey(
        DocumentationPage,
        on_delete=models.CASCADE,
        related_name="sections",
    )

    class Meta:
        ordering = ["order"]
        verbose_name = "Documentation Section"
        verbose_name_plural = "Documentation Sections"

    def __str__(self):
        return f"{self.page.title} – {self.title or 'Section'}"


# ---------------------------------------------------------------------------
# Project Tokenization
# ---------------------------------------------------------------------------

class ProjectTokenizationStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    DEFAULTED = "defaulted", "Defaulted"


class ProjectTokenizationDefaultReason(models.TextChoices):
    NO_LONGER_EXISTS = "no_longer_exists", "Project no longer exists"
    DISSOLVED = "dissolved", "Project dissolved"
    OTHER = "other", "Other"


class ProjectTokenizationQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError("Project tokenization records are permanent and cannot be deleted.")


class ProjectTokenization(models.Model):
    objects = ProjectTokenizationQuerySet.as_manager()

    project = models.OneToOneField(
        Project,
        on_delete=models.PROTECT,
        related_name="tokenization",
    )
    asset = models.OneToOneField(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="project_tokenization",
    )
    supervisor = models.ForeignKey(
        Person,
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
        "auth.User",
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
            models.Index(fields=["status"], name="kanban_proj_status_idx"),
            models.Index(fields=["created_at"], name="kanban_proj_created_idx"),
        ]

    def __str__(self):
        return f"{self.project} tokenized as {self.asset.unit_name}"

    def delete(self, *args, **kwargs):
        raise ValidationError("Project tokenization records are permanent and cannot be deleted.")

    @property
    def is_defaulted(self) -> bool:
        return self.status == ProjectTokenizationStatus.DEFAULTED
