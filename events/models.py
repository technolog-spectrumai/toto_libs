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
    title = models.CharField(max_length=200)
    description = models.TextField()
    location = models.CharField(max_length=200)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()

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

# 📝 Event Registration
class EventRegistration(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='registrations')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='event_registrations')
    registered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('event', 'user')  # Prevent duplicate registrations

    def __str__(self):
        return f"{self.user.username} registered for {self.event.title}"
