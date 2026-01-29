from django.db import models
from django.conf import settings


class Room(models.Model):
    name = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,   # ✔ correct
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    allowed_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,   # ✔ correct
        related_name="allowed_rooms",
        blank=True,
        help_text="Users who can access this room"
    )

    def __str__(self):
        return self.name


class Message(models.Model):
    room = models.ForeignKey(
        "Room",
        on_delete=models.CASCADE,
        related_name="messages"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,   # ✔ correct
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["timestamp"]

    def __str__(self):
        username = self.user.username if self.user else "Anonymous"
        return f"{username}: {self.content[:30]}"
