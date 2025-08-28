from django.db import models
from django.contrib.auth.models import User, Group
from colorfield.fields import ColorField


class Project(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    collaborators = models.ManyToManyField(User, related_name='collaborating_projects')


class Board(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)


class ColorMix(models.Model):
    name = models.CharField(max_length=50, unique=True)

    # Light mode colors
    bg_color_light = ColorField(default='#F3F4F6')
    text_color_light = ColorField(default='#111827')

    # Dark mode colors
    bg_color_dark = ColorField(default='#1a1a1a')
    text_color_dark = ColorField(default='#ffffff')

    def get_colors(self, dark_mode=False):
        return {
            'bg': self.bg_color_dark if dark_mode else self.bg_color_light,
            'text': self.text_color_dark if dark_mode else self.text_color_light
        }

    def __str__(self):
        return self.name


class Column(models.Model):
    board = models.ForeignKey(Board, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    position = models.PositiveIntegerField()

    # Link to reusable color scheme
    color_mix = models.ForeignKey(ColorMix, on_delete=models.SET_NULL, null=True, blank=True)

    def get_theme_colors(self, dark_mode=False):
        if self.color_mix:
            return self.color_mix.get_colors(dark_mode)
        # Fallback if no color_mix assigned
        return {
            'bg': '#F3F4F6' if not dark_mode else '#1a1a1a',
            'text': '#111827' if not dark_mode else '#ffffff'
        }

    def __str__(self):
        return f"{self.name} ({self.board.name})"



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