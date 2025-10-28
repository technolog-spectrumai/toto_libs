from django.db import models
from django.contrib.auth.models import User
from portfolio.models import Venture  # Adjust if your app name is different

# 📁 Event Category
class EventCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name



# 📅 Event Model
class Event(models.Model):
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    title = models.CharField(max_length=200)
    description = models.TextField()
    location = models.CharField(max_length=200)

    venture = models.ForeignKey(
        Venture,
        on_delete=models.CASCADE,
        related_name='events'
    )

    organizer = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='organized_events'
    )

    category = models.ForeignKey(
        EventCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='events'
    )

    def __str__(self):
        return f"{self.title} ({self.venture.name})"

    public = models.BooleanField(default=True, help_text="Check if this event is publicly visible.")
