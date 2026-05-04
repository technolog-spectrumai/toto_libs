from uuid import uuid4
from django.db import models
from toto.socialhub.models import CommunityMember


# 📁 Event Category
class EventCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


# 📅 Event Model
class Event(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    title = models.CharField(max_length=200)
    description = models.TextField()
    location = models.CharField(max_length=200)

    organizer = models.ForeignKey(
        CommunityMember,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='organized_events',
        db_comment="organized_by"
    )

    category = models.ForeignKey(
        EventCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='events',
        db_comment="belongs_to"
    )

    def __str__(self):
        return f"{self.title}"

    public = models.BooleanField(default=True, help_text="Check if this event is publicly visible.")
