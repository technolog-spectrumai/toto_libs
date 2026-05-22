from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone


SEVERITY_CHOICES = [
    ("low", "Low"),
    ("medium", "Medium"),
    ("high", "High"),
    ("critical", "Critical"),
]

PRIORITY_CHOICES = [
    ("low", "Low"),
    ("normal", "Normal"),
    ("high", "High"),
    ("urgent", "Urgent"),
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
    detection = models.ForeignKey(
        "detections.Detection",
        on_delete=models.CASCADE,
        related_name="mobilization_evidence",
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
        unique_together = [("report", "detection")]
        indexes = [
            models.Index(fields=["report", "evidence_role"]),
            models.Index(fields=["report", "weight"]),
        ]

    def __str__(self):
        return f"{self.get_evidence_role_display()} evidence for {self.report}: {self.detection}"


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


class Deployment(models.Model):
    DEPLOYMENT_TYPE_CHOICES = [
        ("evacuation", "Evacuation"),
        ("flood_response", "Flood Response"),
        ("fire_support", "Fire Support"),
        ("shelter_support", "Shelter Support"),
        ("logistics", "Logistics"),
        ("medical", "Medical"),
        ("welfare_check", "Welfare Check"),
        ("reconnaissance", "Reconnaissance"),
        ("mixed", "Mixed"),
        ("other", "Other"),
    ]

    STATUS_CHOICES = [
        ("planned", "Planned"),
        ("active", "Active"),
        ("paused", "Paused"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    event = models.ForeignKey(
        MobilizationEvent,
        on_delete=models.CASCADE,
        related_name="deployments",
    )
    community = models.ForeignKey(
        "socialhub.Community",
        on_delete=models.CASCADE,
        related_name="deployments",
    )
    kanban_mission = models.ForeignKey(
        "kanban.Mission",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mobilization_deployments",
    )
    title = models.CharField(max_length=255)
    deployment_type = models.CharField(max_length=30, choices=DEPLOYMENT_TYPE_CHOICES)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default="normal")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="planned", db_index=True)
    coordinator = models.ForeignKey(
        "people.Person",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="coordinated_deployments",
    )
    objective = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    # Per diem allowance for assigned responders
    per_diem_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    per_diem_asset = models.ForeignKey(
        "assets.Asset",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="deployment_per_diems",
    )

    # Hybrid/partial deployment
    is_hybrid = models.BooleanField(
        default=False,
        help_text="Partial deployment — responders split time with other duties",
    )
    hybrid_time_percent = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Percentage of time allocated to this deployment (0–100)",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["event", "status"]),
            models.Index(fields=["community", "status"]),
            models.Index(fields=["status", "priority"]),
        ]

    def clean(self):
        if self.event_id and self.community_id:
            if self.event.community_id != self.community_id:
                raise ValidationError(
                    "Deployment community must match its event's community."
                )
        if self.hybrid_time_percent is not None and not (0 <= self.hybrid_time_percent <= 100):
            raise ValidationError({"hybrid_time_percent": "Must be between 0 and 100."})

    @property
    def is_partial(self):
        return self.is_hybrid

    def __str__(self):
        return f"[{self.get_status_display()}] {self.title}"


class DeploymentAssignment(models.Model):
    ROLE_CHOICES = [
        ("lead", "Lead"),
        ("deputy", "Deputy"),
        ("driver", "Driver"),
        ("medic", "Medic"),
        ("logistics", "Logistics"),
        ("communicator", "Communicator"),
        ("responder", "Responder"),
        ("volunteer", "Volunteer"),
    ]

    ASSIGNMENT_STATUS_CHOICES = [
        ("assigned", "Assigned"),
        ("confirmed", "Confirmed"),
        ("active", "Active"),
        ("released", "Released"),
        ("completed", "Completed"),
        ("no_show", "No Show"),
    ]

    deployment = models.ForeignKey(
        Deployment,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    responder = models.ForeignKey(
        Responder,
        on_delete=models.CASCADE,
        related_name="deployment_assignments",
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="responder")
    status = models.CharField(
        max_length=20,
        choices=ASSIGNMENT_STATUS_CHOICES,
        default="assigned",
        db_index=True,
    )
    assigned_by = models.ForeignKey(
        "people.Person",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="made_deployment_assignments",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = [("deployment", "responder")]
        indexes = [
            models.Index(fields=["deployment", "status"]),
            models.Index(fields=["responder", "status"]),
        ]

    def __str__(self):
        return f"{self.responder} → {self.deployment} ({self.get_role_display()})"


class EvacuationRoute(models.Model):
    ROUTE_TYPE_CHOICES = [
        ("evacuation", "Evacuation"),
        ("supply", "Supply"),
        ("medical", "Medical"),
        ("patrol", "Patrol"),
        ("other", "Other"),
    ]

    STATUS_CHOICES = [
        ("planned", "Planned"),
        ("active", "Active"),
        ("blocked", "Blocked"),
        ("cleared", "Cleared"),
    ]

    event = models.ForeignKey(
        MobilizationEvent,
        on_delete=models.CASCADE,
        related_name="evac_routes",
    )
    route = models.ForeignKey(
        "locations.Route",
        on_delete=models.CASCADE,
        related_name="mobilization_evac_routes",
    )
    name = models.CharField(max_length=255)
    route_type = models.CharField(max_length=20, choices=ROUTE_TYPE_CHOICES, default="evacuation")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="planned")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["route_type", "name"]

    def __str__(self):
        return f"{self.get_route_type_display()} route: {self.name}"


class DeploymentRoute(models.Model):
    ROUTE_TYPE_CHOICES = [
        ("primary", "Primary"),
        ("alternate", "Alternate"),
        ("supply", "Supply"),
        ("retreat", "Retreat"),
        ("other", "Other"),
    ]

    deployment = models.ForeignKey(
        Deployment,
        on_delete=models.CASCADE,
        related_name="routes",
    )
    route = models.ForeignKey(
        "locations.Route",
        on_delete=models.CASCADE,
        related_name="deployment_routes",
    )
    route_type = models.CharField(max_length=20, choices=ROUTE_TYPE_CHOICES, default="primary")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["route_type"]

    def __str__(self):
        return f"{self.get_route_type_display()} route for {self.deployment}"


class DeploymentEquipment(models.Model):
    deployment = models.ForeignKey(
        Deployment,
        on_delete=models.CASCADE,
        related_name="equipment",
    )
    item = models.ForeignKey(
        "inventory.RealWorldObject",
        on_delete=models.CASCADE,
        related_name="deployment_equipment",
    )
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    notes = models.TextField(blank=True)
    allocated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("deployment", "item")]
        ordering = ["item__name"]

    def __str__(self):
        return f"{self.item.name} × {self.quantity} → {self.deployment}"


class Intervention(models.Model):
    INTERVENTION_TYPE_CHOICES = [
        ("evacuation_pickup", "Evacuation Pickup"),
        ("welfare_check", "Welfare Check"),
        ("first_aid", "First Aid"),
        ("transport", "Transport"),
        ("supply_delivery", "Supply Delivery"),
        ("damage_report", "Damage Report"),
        ("road_closure", "Road Closure"),
        ("sandbagging", "Sandbagging"),
        ("other", "Other"),
    ]

    STATUS_CHOICES = [
        ("todo", "To Do"),
        ("assigned", "Assigned"),
        ("in_progress", "In Progress"),
        ("blocked", "Blocked"),
        ("done", "Done"),
        ("cancelled", "Cancelled"),
    ]

    deployment = models.ForeignKey(
        Deployment,
        on_delete=models.CASCADE,
        related_name="interventions",
    )
    kanban_task = models.ForeignKey(
        "kanban.Task",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mobilization_interventions",
    )
    title = models.CharField(max_length=255)
    intervention_type = models.CharField(max_length=30, choices=INTERVENTION_TYPE_CHOICES)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default="normal")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="todo", db_index=True)
    assigned_to = models.ForeignKey(
        Responder,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="interventions",
    )
    reported_by = models.ForeignKey(
        "people.Person",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reported_interventions",
    )
    description = models.TextField(blank=True)
    outcome_notes = models.TextField(blank=True)
    effect_description = models.TextField(blank=True, help_text="Observed effects / impact of this intervention")
    is_required = models.BooleanField(default=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Review
    reviewer = models.ForeignKey(
        "people.Person",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_interventions",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    # Cost
    estimated_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    actual_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    cost_asset = models.ForeignKey(
        "assets.Asset",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="intervention_costs",
    )
    cost_center = models.CharField(max_length=100, blank=True)

    # Reward for responder
    reward_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    reward_asset = models.ForeignKey(
        "assets.Asset",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="intervention_rewards",
    )
    reward_currency = models.ForeignKey(
        "assets.Currency",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="intervention_rewards",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["deployment", "status"]),
            models.Index(fields=["deployment", "is_required", "status"]),
            models.Index(fields=["assigned_to", "status"]),
        ]

    def __str__(self):
        return f"[{self.get_status_display()}] {self.title}"


class EmergencyStatus(models.Model):
    """
    Declared state of emergency for a community and/or zone, linked to a mobilization event.
    When active, grants special privileges: assets and inventory items belonging to the
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

    # Special privileges granted under this emergency
    allows_asset_requisition = models.BooleanField(
        default=True,
        help_text="Community/zone assets can be requisitioned for deployment use",
    )
    allows_inventory_access = models.BooleanField(
        default=True,
        help_text="Inventory items at community/zone sites become available as hybrid equipment",
    )
    allows_route_commandeering = models.BooleanField(
        default=False,
        help_text="Emergency vehicles may commandeer routes within this zone",
    )

    # Emergency tax — levy collected during the emergency to fund response
    emergency_tax_rate = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Tax rate applied during emergency (e.g. 0.0250 = 2.5%)",
    )
    emergency_tax_asset = models.ForeignKey(
        "assets.Asset",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="emergency_tax_statuses",
        help_text="Asset/currency in which the emergency tax is denominated",
    )
    emergency_tax_account = models.ForeignKey(
        "assets.LedgerAccount",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="emergency_tax_statuses",
        help_text="Ledger account where emergency tax revenue is collected",
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
        Deployment,
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
