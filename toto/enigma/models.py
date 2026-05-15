# chat/models.py
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Room(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    participants = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="chat_rooms",
        blank=True,
    )
    people = models.ManyToManyField(
        "socialhub.Person",
        through="Participant",
        related_name="enigma_rooms",
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Participant(models.Model):
    room = models.ForeignKey(
        Room,
        on_delete=models.CASCADE,
        related_name="chat_participants",
    )
    person = models.ForeignKey(
        "socialhub.Person",
        on_delete=models.CASCADE,
        related_name="chat_participations",
        null=True,
        blank=True,
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["person__display_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["room", "person"],
                condition=models.Q(person__isnull=False),
                name="unique_enigma_room_person",
            ),
            models.CheckConstraint(
                check=models.Q(person__isnull=False),
                name="enigma_participant_must_have_person",
            ),
        ]

    def __str__(self):
        return f"{self.display_name} in {self.room.name}"

    def clean(self):
        super().clean()
        if not self.person_id:
            raise ValidationError("Participants must be human users (person required).")

    @property
    def display_name(self):
        if self.person:
            return self.person.full_name
        return "Unknown participant"

    @property
    def avatar_url(self):
        if self.person and self.person.avatar:
            return self.person.avatar.url
        return "/static/img/avatars/default.png"

    @property
    def participant_type(self):
        return "human"
