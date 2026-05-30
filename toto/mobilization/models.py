from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone


SEVERITY_CHOICES = [
    ("low", "Low"),
    ("medium", "Medium"),
    ("high", "High"),
    ("critical", "Critical"),
]


class IncidentType(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=80, default="fa-solid fa-triangle-exclamation")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.name


class AchievementBadge(models.Model):
    CATEGORY_CHOICES = [
        ("service", "Service"),
        ("rescue", "Rescue"),
        ("leadership", "Leadership"),
        ("training", "Training"),
        ("special", "Special"),
    ]

    name = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=100, default="fa-solid fa-medal")
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, default="service")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["category", "order", "name"]

    def __str__(self):
        return self.name


class PersonAchievement(models.Model):
    person = models.ForeignKey(
        "people.Person",
        on_delete=models.CASCADE,
        related_name="mobilization_achievements",
    )
    badge = models.ForeignKey(
        AchievementBadge,
        on_delete=models.CASCADE,
        related_name="awarded_to",
    )
    deployment = models.ForeignKey(
        "response.Deployment",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="achievement_awards",
    )
    awarded_by = models.ForeignKey(
        "people.Person",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="awarded_achievements",
    )
    awarded_at = models.DateTimeField(default=timezone.now)
    note = models.TextField(blank=True)

    class Meta:
        unique_together = [("person", "badge")]
        ordering = ["-awarded_at"]

    def __str__(self):
        return f"{self.person} — {self.badge}"


class Responder(models.Model):
    CURRENT_STATUS_CHOICES = [
        ("off_duty", "Off Duty"),
        ("available", "Available"),
        ("standby", "Standby"),
        ("responding", "Responding"),
        ("unavailable", "Unavailable"),
    ]

    person = models.OneToOneField(
        "people.Person",
        on_delete=models.CASCADE,
        related_name="responder_profile",
    )
    communities = models.ManyToManyField(
        "socialhub.Community",
        blank=True,
        related_name="responders",
    )
    is_active = models.BooleanField(default=True)
    is_trained = models.BooleanField(default=False)
    is_background_checked = models.BooleanField(default=False)
    current_status = models.CharField(
        max_length=20,
        choices=CURRENT_STATUS_CHOICES,
        default="off_duty",
        db_index=True,
    )
    last_status_changed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["current_status"]),
            models.Index(fields=["is_active", "current_status"]),
        ]

    def clean(self):
        if not self.person_id:
            return
        from toto.people.models import Person as _Person
        from toto.people.civic import is_committed_citizen
        p = _Person.objects.prefetch_related("communities").get(pk=self.person_id)
        if is_committed_citizen(p):
            return
        if p.communities.filter(is_federal_tribe=True).exists():
            return
        raise ValidationError(
            "Responders must be committed citizens or members of a federal tribe community."
        )

    def __str__(self):
        return f"Responder: {self.person}"


class ResponderSkill(models.Model):
    LEVEL_CHOICES = [
        ("basic", "Basic"),
        ("trained", "Trained"),
        ("certified", "Certified"),
        ("professional", "Professional"),
    ]

    responder = models.ForeignKey(
        Responder,
        on_delete=models.CASCADE,
        related_name="skills",
    )
    skill = models.ForeignKey(
        "competence.SkillBadge",
        on_delete=models.CASCADE,
        related_name="mobilization_responder_skills",
    )
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default="basic")
    verified_by = models.ForeignKey(
        "people.Person",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="verified_responder_skills",
    )
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("responder", "skill")]
        indexes = [
            models.Index(fields=["responder", "level"]),
        ]

    def __str__(self):
        return f"{self.responder} — {self.skill} ({self.get_level_display()})"


class MobilizationReport(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("submitted", "Submitted"),
        ("reviewed", "Reviewed"),
        ("enacted", "Enacted"),
        ("rejected", "Rejected"),
        ("closed", "Closed"),
    ]

    community = models.ForeignKey(
        "socialhub.Community",
        on_delete=models.CASCADE,
        related_name="mobilization_reports",
    )
    title = models.CharField(max_length=255)
    incident_type = models.ForeignKey(
        IncidentType,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reports",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft", db_index=True)
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default="low")

    submitted_by = models.ForeignKey(
        "people.Person",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="submitted_mobilization_reports",
    )
    reviewed_by = models.ForeignKey(
        "people.Person",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_mobilization_reports",
    )
    enacted_by = models.ForeignKey(
        "people.Person",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="enacted_mobilization_reports",
    )

    summary = models.TextField(blank=True)
    justification = models.TextField(blank=True)
    decision_notes = models.TextField(blank=True)

    enacted_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["community", "status"]),
            models.Index(fields=["status", "severity"]),
        ]

    def __str__(self):
        return f"[{self.get_status_display()}] {self.title} ({self.community})"


class MobilizationReportEvidence(models.Model):
    EVIDENCE_ROLE_CHOICES = [
        ("primary", "Primary"),
        ("supporting", "Supporting"),
        ("context", "Context"),
        ("contradictory", "Contradictory"),
    ]

    WEIGHT_CHOICES = [
        ("low", "Low"),
        ("normal", "Normal"),
        ("high", "High"),
    ]

    report = models.ForeignKey(
        MobilizationReport,
        on_delete=models.CASCADE,
        related_name="evidence_links",
    )
    incident = models.ForeignKey(
        "incidents.Incident",
        on_delete=models.CASCADE,
        related_name="mobilization_evidence",
        null=True,
        blank=True,
    )
    evidence_role = models.CharField(max_length=20, choices=EVIDENCE_ROLE_CHOICES, default="supporting")
    weight = models.CharField(max_length=10, choices=WEIGHT_CHOICES, default="normal")
    note = models.TextField(blank=True)
    added_by = models.ForeignKey(
        "people.Person",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="added_mobilization_evidence",
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("report", "incident")]
        indexes = [
            models.Index(fields=["report", "evidence_role"]),
            models.Index(fields=["report", "weight"]),
        ]

    def __str__(self):
        return f"{self.get_evidence_role_display()} evidence for {self.report}: {self.incident}"


class MobilizationEvent(models.Model):
    STATUS_CHOICES = [
        ("standby", "Standby"),
        ("active", "Active"),
        ("resolved", "Resolved"),
        ("cancelled", "Cancelled"),
    ]

    community = models.ForeignKey(
        "socialhub.Community",
        on_delete=models.CASCADE,
        related_name="mobilization_events",
    )
    source_report = models.ForeignKey(
        MobilizationReport,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mobilization_events",
    )
    scheduled_event = models.ForeignKey(
        "events.ScheduledEvent",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mobilization_events",
    )
    kanban_campaign = models.ForeignKey(
        "kanban.Campaign",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mobilization_events",
    )
    title = models.CharField(max_length=255)
    incident_type = models.ForeignKey(
        IncidentType,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="events",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="standby", db_index=True)
    coordinator = models.ForeignKey(
        "people.Person",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="coordinated_mobilization_events",
    )
    description = models.TextField(blank=True)
    is_hybrid = models.BooleanField(
        default=False,
        help_text="Hybrid event — some deployments are partial/shared duty",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["community", "status"]),
        ]

    def clean(self):
        if self.source_report_id and self.community_id:
            if self.source_report.community_id != self.community_id:
                raise ValidationError(
                    "MobilizationEvent community must match its source report's community."
                )

    def __str__(self):
        return f"[{self.get_status_display()}] {self.title} ({self.community})"


class EmergencyStatus(models.Model):
    """
    Declared state of emergency for a community and/or zone, linked to a mobilization event.
    When active, grants special privileges: inventory items belonging to the
    community/zone can be marked as hybrid equipment and allocated to deployments.
    """

    LEVEL_CHOICES = [
        ("watch", "Watch"),
        ("warning", "Warning"),
        ("emergency", "Emergency"),
        ("critical_emergency", "Critical Emergency"),
    ]

    STATUS_CHOICES = [
        ("active", "Active"),
        ("lifted", "Lifted"),
        ("expired", "Expired"),
    ]

    event = models.ForeignKey(
        MobilizationEvent,
        on_delete=models.CASCADE,
        related_name="emergency_statuses",
    )
    community = models.ForeignKey(
        "socialhub.Community",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="emergency_statuses",
    )
    zone = models.ForeignKey(
        "locations.Zone",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="emergency_statuses",
    )
    level = models.CharField(max_length=30, choices=LEVEL_CHOICES, default="warning")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active", db_index=True)
    declared_by = models.ForeignKey(
        "people.Person",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="declared_emergency_statuses",
    )
    declared_at = models.DateTimeField(default=timezone.now)
    lifted_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    allows_inventory_access = models.BooleanField(
        default=True,
        help_text="Inventory items at community/zone sites become available as hybrid equipment",
    )
    allows_route_commandeering = models.BooleanField(
        default=False,
        help_text="Emergency vehicles may commandeer routes within this zone",
    )
    allows_asset_requisition = models.BooleanField(
        default=False,
        help_text="Community/zone assets may be requisitioned for deployment use",
    )
    emergency_tax_rate = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Levy applied to transactions during the emergency (e.g. 0.0250 = 2.5%)",
    )

    source_proposal = models.ForeignKey(
        "assembly.AssemblyProposal",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="emergency_declarations",
        help_text="Assembly proposal that voted to authorize this emergency status",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["event", "status"]),
            models.Index(fields=["community", "status"]),
            models.Index(fields=["zone", "status"]),
        ]
        verbose_name_plural = "Emergency statuses"

    def clean(self):
        if not self.community_id and not self.zone_id:
            raise ValidationError("An emergency status must target either a community or a zone (or both).")

    @property
    def is_active(self):
        if self.status != "active":
            return False
        if self.expires_at and timezone.now() > self.expires_at:
            return False
        return True

    def lift(self, lifted_by=None):
        self.status = "lifted"
        self.lifted_at = timezone.now()
        self.save()

    def __str__(self):
        target = str(self.community or self.zone or "—")
        return f"[{self.get_level_display()}] Emergency — {target} ({self.get_status_display()})"


class EmergencyEquipmentAccess(models.Model):
    """
    Tracks an inventory item that has been granted hybrid access under an emergency status.
    The item remains owned by the community/zone but can be allocated to deployments.
    """

    emergency = models.ForeignKey(
        EmergencyStatus,
        on_delete=models.CASCADE,
        related_name="equipment_accesses",
    )
    item = models.ForeignKey(
        "inventory.RealWorldObject",
        on_delete=models.CASCADE,
        related_name="emergency_accesses",
    )
    deployment = models.ForeignKey(
        "response.Deployment",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="emergency_equipment",
    )
    is_hybrid = models.BooleanField(
        default=True,
        help_text="Item shared under emergency — owner retains nominal ownership",
    )
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    authorized_by = models.ForeignKey(
        "people.Person",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="authorized_emergency_equipment",
    )
    authorized_at = models.DateTimeField(auto_now_add=True)
    returned_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = [("emergency", "item")]
        ordering = ["item__name"]

    def __str__(self):
        return f"Emergency access: {self.item.name} (hybrid={self.is_hybrid})"
