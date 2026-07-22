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

