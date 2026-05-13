from django.db import models
from django.contrib.auth.models import User
from toto.core.domain import DomainEntity
from toto.socialhub.models import Person
from toto.verbena.models import AbstractPage, AbstractSection


# 3‑level hierarchy for missions
THREE_SCALE = [
    (1, "Low"),
    (2, "Medium"),
    (3, "High"),
]

# Fibonacci scale for task weight
FIB_SCALE = [
    (1, "Tiny"),
    (2, "Small"),
    (3, "Medium"),
    (5, "Big"),
    (8, "Large"),
]


# 📁 Project
class Project(DomainEntity):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(Person, on_delete=models.CASCADE)
    collaborators = models.ManyToManyField(User, related_name='collaborating_projects')

    def __str__(self):
        return self.name


# 📦 Column
class Column(DomainEntity):
    graph_node_type = "TaskStatus"
    project = models.ForeignKey(Project, on_delete=models.CASCADE, db_column="belongs_to_project")
    name = models.CharField(max_length=100)
    position = models.PositiveIntegerField()
    can_add_task = models.BooleanField(default=False)
    auditors = models.ManyToManyField(
        User,
        related_name="audited_columns",
        blank=True,
        help_text="Users who can move tasks into this column"
    )

    def __str__(self):
        return self.name


# 📣 Campaign
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
        help_text="Operational zone for this campaign, if it is geographically scoped.",
    )

    def __str__(self):
        return self.name


# 🎯 Mission
class Mission(DomainEntity):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="missions")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    urgency = models.IntegerField(
        choices=THREE_SCALE,
        default=2  # Medium
    )
    impact = models.IntegerField(
        choices=THREE_SCALE,
        default=2  # Medium
    )

    location = models.ForeignKey(
        "locations.Address",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="missions",
        help_text="Specific mission location, if applicable.",
    )

    route = models.ForeignKey(
        "locations.Route",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="missions",
        help_text="Route connected to this mission, if movement is involved.",
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



# 🚀 Sprint
class Sprint(DomainEntity):
    name = models.CharField(max_length=100)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()

    def __str__(self):
        return self.name


# 📝 Task
class Task(DomainEntity):
    mission = models.ForeignKey(Mission, on_delete=models.CASCADE, related_name="tasks")
    column = models.ForeignKey(Column, on_delete=models.CASCADE, related_name='tasks')
    sprint = models.ForeignKey(Sprint, on_delete=models.SET_NULL, null=True, blank=True, related_name='tasks')

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    assignee = models.ForeignKey(Person, on_delete=models.SET_NULL, null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    position = models.PositiveIntegerField(default=0)

    weight = models.IntegerField(
        choices=FIB_SCALE,
        default=1  # Tiny
    )

    metadata = models.JSONField(blank=True, null=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.title

    @property
    def weight_label(self):
        return dict(FIB_SCALE).get(self.weight, self.weight)


# 📄 Documentation Page
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


# 📝 Documentation Section
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
