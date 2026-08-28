"""The Business Center's integration root.

Company owns the legal entity, the people attached to it, the department tree
and the share register. It is the ONLY Business Center app allowed to reach
into the rest of Zenobia — people, events, the vault — and nothing in Zenobia
imports it back. `tests/test_boundary.py` enforces both directions.

Revived from the deleted `irena` host (`goodbye_irena^`), where these models
lived in `toto.company` and `toto.departments`. The two are folded into one app
here; the constraint names are re-prefixed `bc_` because these tables are new
on this host and no deployed migration is being touched.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from toto.core.domain import DomainEntity


class CompanyForm(models.TextChoices):
    GENERIC = "generic", "Generic company"
    PL_SP_ZOO = "pl_sp_zoo", "Polish sp. z o.o."
    PL_PSA = "pl_psa", "Polish P.S.A."


class Company(DomainEntity):
    """The legal entity whose structure and decisions this app records."""

    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, blank=True)
    form = models.CharField(
        max_length=32,
        choices=CompanyForm.choices,
        default=CompanyForm.GENERIC,
        help_text="Starter-rule selector and display fact, not a runtime branch.",
    )
    legal_form_label = models.CharField(max_length=200, blank=True)
    registry_no = models.CharField(max_length=64, blank=True)
    tax_no = models.CharField(max_length=32, blank=True)
    statistical_no = models.CharField(max_length=32, blank=True)
    seat = models.TextField(blank=True)
    share_capital = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    capital_currency = models.CharField(max_length=8, default="PLN", blank=True)

    logo = models.ImageField(
        upload_to="company_logos/",
        null=True,
        blank=True,
        help_text="Optional logo for this company.",
    )

    #: The statute, either as plain text or as a protected file in the vault.
    #: SET_NULL and never CASCADE — deleting a file must not delete the company
    #: that adopted it. This is socialhub's `Community.statute` precedent
    #: exactly. A reader reaches the file through the vault's own download
    #: route, which enforces may_read; the template never decides that.
    statute_text = models.TextField(blank=True)
    statute_file = models.ForeignKey(
        "vault.VaultFile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The company's statute, as a file in the vault.",
    )

    community_ref = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional Social Hub community slug or external community reference.",
    )
    constitutive_document_ref = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional vault path, document UID, registry URL or external evidence reference.",
    )
    active = models.BooleanField(default=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "company"
        verbose_name_plural = "companies"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name) or "company"
        return super().save(*args, **kwargs)


class Party(DomainEntity):
    """A governance actor: person, organisation, or external holder.

    `person` is a link, not ownership: `toto.people` owns the Person and knows
    nothing about this app. PROTECT so a Person who holds shares cannot be
    deleted out from under the register.
    """

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="parties")
    name = models.CharField(max_length=200)
    person = models.ForeignKey(
        "people.Person",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="bc_parties",
    )
    is_organisation = models.BooleanField(default=False)
    registry_no = models.CharField(max_length=64, blank=True)
    tax_no = models.CharField(max_length=32, blank=True)
    address = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("company", "name")
        verbose_name_plural = "parties"
        constraints = [
            models.CheckConstraint(
                check=models.Q(is_organisation=False) | models.Q(person__isnull=True),
                name="bc_org_party_has_no_person",
            ),
            models.UniqueConstraint(fields=["company", "name"], name="bc_one_party_name_per_company"),
        ]

    def __str__(self):
        return self.name

    @property
    def user(self):
        if self.person_id is None:
            return None
        return self.person.user


class ShareClass(DomainEntity):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="share_classes")
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140)
    votes_per_unit = models.DecimalField(max_digits=16, decimal_places=6, default=Decimal("1"))
    nominal_value = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    active = models.BooleanField(default=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("company", "name")
        verbose_name_plural = "share classes"
        constraints = [
            models.UniqueConstraint(fields=["company", "slug"], name="bc_one_share_slug_per_company"),
            models.CheckConstraint(check=models.Q(votes_per_unit__gte=0), name="bc_votes_per_unit_non_negative"),
        ]

    def __str__(self):
        return f"{self.name} ({self.company})"


class ShareHolding(DomainEntity):
    """One party's position in one share class, closed by `until` when it changes.

    Units are exact: `Decimal(24, 6)`, never float. The register is derived from
    OwnershipEvents and must add up to the last unit.
    """

    party = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="share_holdings")
    share_class = models.ForeignKey(ShareClass, on_delete=models.PROTECT, related_name="holdings")
    units = models.DecimalField(max_digits=24, decimal_places=6)
    since = models.DateField(default=timezone.localdate)
    until = models.DateField(null=True, blank=True)
    source_event = models.ForeignKey(
        "company.OwnershipEvent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resulting_holdings",
    )
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("share_class", "party")
        constraints = [
            models.UniqueConstraint(
                fields=["party", "share_class"],
                condition=models.Q(until__isnull=True),
                name="bc_one_live_holding_per_party_class",
            ),
            models.CheckConstraint(check=models.Q(units__gt=0), name="bc_holding_units_positive"),
            models.CheckConstraint(
                check=models.Q(until__isnull=True) | models.Q(until__gte=models.F("since")),
                name="bc_holding_dates_ordered",
            ),
        ]

    @property
    def votes(self):
        return self.units * self.share_class.votes_per_unit

    def __str__(self):
        return f"{self.party}: {self.units} x {self.share_class.name}"


class OwnershipEventType(models.TextChoices):
    ISSUE = "issue", "Issue"
    TRANSFER = "transfer", "Transfer"
    SPLIT = "split", "Split"
    CORRECTION = "correction", "Correction"


class OwnershipEvent(DomainEntity):
    """Append-only. The register is a projection of these; they are the truth."""

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="ownership_events")
    event_type = models.CharField(max_length=24, choices=OwnershipEventType.choices)
    source_party = models.ForeignKey(
        Party, on_delete=models.PROTECT, null=True, blank=True, related_name="ownership_events_out"
    )
    target_party = models.ForeignKey(
        Party, on_delete=models.PROTECT, null=True, blank=True, related_name="ownership_events_in"
    )
    source_share_class = models.ForeignKey(
        ShareClass, on_delete=models.PROTECT, null=True, blank=True, related_name="ownership_events_from"
    )
    target_share_class = models.ForeignKey(
        ShareClass, on_delete=models.PROTECT, null=True, blank=True, related_name="ownership_events_to"
    )
    source_units = models.DecimalField(max_digits=24, decimal_places=6, null=True, blank=True)
    target_units = models.DecimalField(max_digits=24, decimal_places=6, null=True, blank=True)
    effective_on = models.DateField(default=timezone.localdate)
    authority_reference = models.CharField(max_length=200, blank=True)
    evidence_ref = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional vault path, document UID, registry URL or external evidence reference.",
    )
    evidence_hash = models.CharField(max_length=64, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bc_ownership_events_recorded",
    )
    recorded_at = models.DateTimeField(auto_now_add=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ("-effective_on", "-recorded_at")
        indexes = [
            models.Index(fields=["company", "event_type", "effective_on"]),
            models.Index(fields=["source_party", "target_party"]),
        ]

    def save(self, *args, **kwargs):
        if self.pk and not kwargs.pop("_allow_update", False):
            raise ValidationError("Ownership events are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Ownership events cannot be deleted.")

    def __str__(self):
        return f"{self.event_type} on {self.effective_on}"


# ---------------------------------------------------------------------------
# The department tree. Folded in from irena's `toto.departments`.
# ---------------------------------------------------------------------------


class Department(DomainEntity):
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="departments",
    )
    name = models.CharField(max_length=180)
    slug = models.SlugField(max_length=200)
    description = models.TextField(blank=True)
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
    )
    head = models.ForeignKey(
        Party,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="departments_headed",
    )
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=80, blank=True)
    address = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("company", "name")
        constraints = [
            models.UniqueConstraint(
                fields=["company", "slug"],
                name="bc_one_department_slug_per_company",
            ),
            models.CheckConstraint(
                check=~models.Q(parent=models.F("id")),
                name="bc_department_not_own_parent",
            ),
        ]

    def clean(self):
        if self.parent_id and self.parent.company_id != self.company_id:
            raise ValidationError({"parent": "A parent department must belong to the same company."})
        if self.head_id and self.head.company_id != self.company_id:
            raise ValidationError({"head": "The department head must be a Party in this company."})
        ancestor = self.parent
        seen = {self.pk} if self.pk else set()
        while ancestor is not None:
            if ancestor.pk in seen:
                raise ValidationError({"parent": "Department hierarchy cannot contain a cycle."})
            seen.add(ancestor.pk)
            ancestor = ancestor.parent

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name) or "department"
        self.clean()
        return super().save(*args, **kwargs)

    def ancestors(self):
        rows = []
        current = self.parent
        seen = set()
        while current is not None and current.pk not in seen:
            seen.add(current.pk)
            rows.append(current)
            current = current.parent
        return list(reversed(rows))

    def __str__(self):
        return f"{self.name} ({self.company})"


class DepartmentMembership(DomainEntity):
    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name="memberships",
    )
    party = models.ForeignKey(
        Party,
        on_delete=models.PROTECT,
        related_name="department_memberships",
    )
    title = models.CharField(max_length=160, blank=True)
    is_leadership = models.BooleanField(default=False)
    can_record_decisions = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    joined_on = models.DateField(default=timezone.localdate)
    left_on = models.DateField(null=True, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("department", "-is_leadership", "party__name")
        constraints = [
            models.UniqueConstraint(
                fields=["department", "party"],
                condition=models.Q(active=True),
                name="bc_one_active_department_membership",
            ),
            models.CheckConstraint(
                check=models.Q(left_on__isnull=True) | models.Q(left_on__gte=models.F("joined_on")),
                name="bc_department_membership_dates",
            ),
        ]

    def clean(self):
        if self.party_id and self.department_id:
            if self.party.company_id != self.department.company_id:
                raise ValidationError({"party": "A department member must be a Party in the same company."})

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.party} in {self.department}"


class CompanyMembership(DomainEntity):
    """A person's membership of a company: the job title and the home department.

    New in this revival — irena had only department seats. `toto.people` owns
    the Person; this row links to it and PROTECTs it. The job title is
    deliberately plain text: no role vocabulary, no permission meaning.
    Authorization reads `active`, never what the title says.
    """

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="memberships")
    person = models.ForeignKey(
        "people.Person",
        on_delete=models.PROTECT,
        related_name="bc_company_memberships",
    )
    job_title = models.CharField(max_length=200, blank=True)
    primary_department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="primary_members",
    )
    active = models.BooleanField(default=True)
    joined_on = models.DateField(default=timezone.localdate)
    left_on = models.DateField(null=True, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("company", "person__display_name")
        constraints = [
            models.UniqueConstraint(
                fields=["company", "person"],
                condition=models.Q(active=True),
                name="bc_one_active_company_membership",
            ),
            models.CheckConstraint(
                check=models.Q(left_on__isnull=True) | models.Q(left_on__gte=models.F("joined_on")),
                name="bc_company_membership_dates",
            ),
        ]

    def clean(self):
        if self.primary_department_id and self.primary_department.company_id != self.company_id:
            raise ValidationError(
                {"primary_department": "The primary department must belong to the same company."})

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.person} at {self.company}"


# ---------------------------------------------------------------------------
# Actions — the drafts that become blocks. Stage 2.
# ---------------------------------------------------------------------------


class ActionStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    RECORDED = "recorded", "Recorded"


class ActionKind(models.TextChoices):
    RESOLUTION = "resolution", "Resolution"
    APPOINTMENT = "appointment", "Appointment"
    STATEMENT = "statement", "Statement"
    OTHER = "other", "Other"


class CompanyAction(DomainEntity):
    """An important thing a company did, written as a draft and then recorded.

    Recording freezes the complete Company-fact payload into a block and
    appends it. After that the row is read-only: the block is the record, and
    a draft that could still be edited afterwards would make the two disagree.

    **There is no `supersedes`, no `corrects`, no `reverses`.** The chain does
    not interpret relationships, so neither does this. An action that corrects
    an earlier one says so in its own `body`, citing the earlier block's id as
    ordinary text — which is exactly what a paper minute book does.
    """

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="actions")
    title = models.CharField(max_length=255)
    body = models.TextField(
        help_text="What was decided. Cite an earlier block by its id if you need to — "
                  "it stays ordinary text.",
    )
    kind = models.CharField(max_length=24, choices=ActionKind.choices,
                            default=ActionKind.RESOLUTION)
    status = models.CharField(max_length=16, choices=ActionStatus.choices,
                              default=ActionStatus.DRAFT)

    #: The block this became. A uid, not a foreign key: `toto.ledger` must not
    #: gain a reverse relation into this app, and the block outlives this row.
    block_uid = models.UUIDField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bc_actions_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    recorded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-recorded_at", "-created_at")
        indexes = [
            models.Index(fields=["company", "status"], name="bc_action_company_idx"),
        ]

    def mark_recorded(self):
        self.status = ActionStatus.RECORDED
        self.recorded_at = self.recorded_at or timezone.now()
        self.save(update_fields=["status", "recorded_at", "updated_at"],
                  _allow_update=True)

    def save(self, *args, **kwargs):
        allow_update = kwargs.pop("_allow_update", False)
        if self.pk and not allow_update:
            previous = type(self).objects.get(pk=self.pk)
            if previous.status == ActionStatus.RECORDED:
                raise ValidationError(
                    "A recorded action is frozen — its block is the record."
                )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status == ActionStatus.RECORDED:
            raise ValidationError("A recorded action cannot be deleted.")
        return super().delete(*args, **kwargs)

    @property
    def is_recorded(self):
        return self.status == ActionStatus.RECORDED

    def __str__(self):
        return self.title


class DepartmentDecisionStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    FINAL = "final", "Final"
    SUPERSEDED = "superseded", "Superseded"


class DepartmentDecision(DomainEntity):
    """A department's own decision. Deferred from Stage 1 because it hashes.

    irena's model, kept — including `supersedes`, which lives here rather than
    on the chain: a department tracking which of its decisions replaced which
    is ordinary domain data. The LEDGER still refuses to interpret it; if this
    decision is recorded, the relationship travels inside the frozen payload as
    a fact, not as a link the chain follows.
    """

    department = models.ForeignKey(Department, on_delete=models.PROTECT,
                                   related_name="decisions")
    title = models.CharField(max_length=255)
    status = models.CharField(max_length=16, choices=DepartmentDecisionStatus.choices,
                              default=DepartmentDecisionStatus.DRAFT)
    body = models.TextField(blank=True)
    content_hash = models.CharField(max_length=128, editable=False, blank=True)
    evidence_ref = models.CharField(max_length=255, blank=True)
    supersedes = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True,
        related_name="superseded_by",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="bc_department_decisions_created",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-decided_at", "-created_at")

    def content(self):
        return {"title": self.title, "body": self.body,
                "evidence_ref": self.evidence_ref}

    def save(self, *args, **kwargs):
        from toto.ledger.canonical import canonical_payload, digest

        allow_update = kwargs.pop("_allow_update", False)
        if self.pk and not allow_update:
            previous = type(self).objects.get(pk=self.pk)
            if previous.status in {DepartmentDecisionStatus.FINAL,
                                   DepartmentDecisionStatus.SUPERSEDED}:
                raise ValidationError("Final department decisions are immutable.")
        if self.status == DepartmentDecisionStatus.FINAL and self.decided_at is None:
            self.decided_at = timezone.now()
        self.content_hash = digest(canonical_payload(self.content()))
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != DepartmentDecisionStatus.DRAFT:
            raise ValidationError("Final department decisions cannot be deleted.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return self.title
