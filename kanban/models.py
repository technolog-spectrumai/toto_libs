from django.db import models
from django.contrib.auth.models import User, Group


class Project(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    collaborators = models.ManyToManyField(User, related_name='collaborating_projects')


class Board(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)


class Column(models.Model):
    board = models.ForeignKey(Board, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    position = models.PositiveIntegerField()


class Task(models.Model):
    column = models.ForeignKey(Column, on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    assignee = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    completed = models.BooleanField(default=False)
    position = models.PositiveIntegerField(default=0)
    story_points = models.PositiveIntegerField(default=1)


class TimePeriod(models.Model):
    start = models.DateTimeField()
    end = models.DateTimeField()

    class Meta:
        abstract = True


# class Event(TimePeriod):
#     title = models.CharField(max_length=200)
#     description = models.TextField(blank=True)
#     location = models.CharField(max_length=255, blank=True)
#     attendees = models.ManyToManyField(User)
#     project = models.ForeignKey(Project, on_delete=models.CASCADE)


class Sprint(TimePeriod):
    name = models.CharField(max_length=100)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    tasks = models.ManyToManyField(Task)


class Role(models.Model):
    name = models.CharField(max_length=50)
    users = models.ManyToManyField(User)
    project = models.ForeignKey(Project, on_delete=models.CASCADE)