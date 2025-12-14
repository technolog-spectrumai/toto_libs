from django.db import models
from django.contrib.auth.models import User
from webfront.models import MetricsPage


# 📁 Project
class Project(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    collaborators = models.ManyToManyField(User, related_name='collaborating_projects')
    metrics_page = models.ForeignKey(
        MetricsPage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="projects",
        help_text="Optional MetricsPage associated with this project"
    )

    def __str__(self):
        return self.name


# 📦 Column
class Column(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    position = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.name} ({self.project.name})"


# 📣 Campaign
class Campaign(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="campaigns")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    metadata = models.JSONField(blank=True, null=True)

    def __str__(self):
        return f"Campaign: {self.name} ({self.project.name})"


# 🎯 Mission
class Mission(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="missions")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    urgency = models.IntegerField(
        choices=[(1, "Low"), (2, "Medium"), (3, "High")],
        default=2
    )
    impact = models.IntegerField(
        choices=[(1, "Low"), (2, "Medium"), (3, "High")],
        default=2
    )

    owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    metadata = models.JSONField(blank=True, null=True)

    def __str__(self):
        return f"Mission: {self.title} ({self.campaign.name})"


# 🚀 Sprint
class Sprint(models.Model):
    name = models.CharField(max_length=100)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()

    def __str__(self):
        return f"Sprint: {self.name} ({self.project.name})"


# 📝 Task
class Task(models.Model):
    mission = models.ForeignKey(Mission, on_delete=models.CASCADE, related_name="tasks")
    column = models.ForeignKey(Column, on_delete=models.CASCADE, related_name='tasks')
    sprint = models.ForeignKey(Sprint, on_delete=models.SET_NULL, null=True, blank=True, related_name='tasks')

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    assignee = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    position = models.PositiveIntegerField(default=0)

    weight = models.PositiveIntegerField(default=1)
    metadata = models.JSONField(blank=True, null=True)

    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.title
