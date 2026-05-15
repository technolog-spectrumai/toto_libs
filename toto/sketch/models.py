from django.db import models
from django.contrib.auth import get_user_model
from toto.vault.models import Bucket

User = get_user_model()

class Board(models.Model):
    id = models.CharField(primary_key=True, max_length=50)
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="boards")
    created_at = models.DateTimeField(auto_now_add=True)
    image = models.ImageField(upload_to="board_images/", null=True, blank=True)
    bucket = models.ForeignKey(
        Bucket,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="boards"
    )

    def __str__(self):
        return self.name


class BoardObject(models.Model):
    board = models.ForeignKey(Board, on_delete=models.CASCADE, related_name="board_objects")
    object_id = models.CharField(max_length=100)  # UUID from frontend
    object_type = models.CharField(max_length=50)  # "path", "text", "shape", etc.
    data = models.JSONField()  # Paper.js JSON export
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.object_type} ({self.object_id})"
