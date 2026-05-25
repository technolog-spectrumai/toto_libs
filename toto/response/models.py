from django.db import models
from django.utils import timezone


PRIORITY_CHOICES = [
    ("low", "Low"),
    ("normal", "Normal"),
    ("high", "High"),
    ("urgent", "Urgent"),
]


class InterventionType(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=80, default="fa-solid fa-hand-holding-medical")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.name


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
        "mobilization.MobilizationEvent",
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
        from django.core.exceptions import ValidationError
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
        "mobilization.Responder",
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


class Intervention(models.Model):
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
    detection = models.ForeignKey(
        "detections.Detection",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mobilization_interventions",
        help_text="Detection this intervention is intended to mitigate",
    )
    title = models.CharField(max_length=255)
    intervention_type = models.ForeignKey(
        InterventionType,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="interventions",
    )
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default="normal")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="todo", db_index=True)
    assigned_to = models.ForeignKey(
        "mobilization.Responder",
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
    estimated_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    is_required = models.BooleanField(default=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    reviewer = models.ForeignKey(
        "people.Person",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_interventions",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

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
        "mobilization.MobilizationEvent",
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
