from django.db import models
from django.contrib.auth.models import User, Group
from colorfield.fields import ColorField
from django.urls import reverse


class Project(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    collaborators = models.ManyToManyField(User, related_name='collaborating_projects')


class Board(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)

    def get_absolute_url(self):
        return reverse('kanban:board-detail', kwargs={'pk': self.pk})


class Column(models.Model):
    board = models.ForeignKey(Board, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    position = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.name} ({self.board.name})"


class Task(models.Model):
    column = models.ForeignKey(Column, on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    assignee = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    position = models.PositiveIntegerField(default=0)


class TimePeriod(models.Model):
    start = models.DateTimeField()
    end = models.DateTimeField()

    class Meta:
        abstract = True


class Sprint(TimePeriod):
    name = models.CharField(max_length=100)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    tasks = models.ManyToManyField(Task)


class Role(models.Model):
    name = models.CharField(max_length=50)
    users = models.ManyToManyField(User)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)