from django.db import models
from django.contrib.auth.models import User
from portfolio.models import Company
from django_jsonform.models.fields import JSONField


# 📁 Project
class Project(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    venture = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='kanban_projects',
        null=True,
        blank=True
    )
    collaborators = models.ManyToManyField(User, related_name='collaborating_projects')

    def __str__(self):
        return self.name


# 📦 Column
class Column(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    position = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.name} ({self.project.name})"


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
    column = models.ForeignKey(Column, on_delete=models.CASCADE, related_name='tasks')
    sprint = models.ForeignKey(Sprint, on_delete=models.SET_NULL, null=True, blank=True, related_name='tasks')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    assignee = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    position = models.PositiveIntegerField(default=0)


    urgency = models.IntegerField(
        choices=[(1, "Low"), (2, "Medium"), (3, "High")],
        default=2
    )
    impact = models.IntegerField(
        choices=[(1, "Low"), (2, "Medium"), (3, "High")],
        default=2
    )
    weight = models.PositiveIntegerField(default=1)

    # ✅ Free-form metadata JSON
    metadata = JSONField(blank=True, null=True)

    def __str__(self):
        return self.title
