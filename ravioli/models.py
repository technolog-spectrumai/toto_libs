from django.db import models
from django.utils.timezone import now
from toto.models import SerializableModel
from community.models import SocialEntity
from events.models import Event


class Tag(models.Model):
    """
    Represents a tag or keyword attached to notes.
    """
    name = models.CharField(max_length=100, unique=True)
    created_at = models.DateTimeField(default=now)

    def __str__(self):
        return self.name


class Note(SerializableModel):
    """
    Represents an Intel Note in the relational database.
    """
    title = models.CharField(max_length=255, db_index=True)
    content = models.TextField(blank=True, null=True)   # main text of the note
    metadata = models.JSONField(blank=True, null=True)  # audit info, provenance, etc.
    created_at = models.DateTimeField(default=now)
    category = models.CharField(max_length=100, blank=True, null=True)  # e.g., "Intel", "Observation", "Report"
    subject = models.ForeignKey(
        SocialEntity,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="related_intel_notes"
    )
    event = models.ForeignKey(
        Event,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="notes_for_event"
    )

    # Tags are simple many-to-many
    tags = models.ManyToManyField(
        Tag,
        blank=True,
        related_name="notes_for_tag"
    )
    is_public = models.BooleanField(default=True, help_text="Mark note as public")

    def __str__(self):
        return self.title
