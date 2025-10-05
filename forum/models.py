# forum/models.py
from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User


class Room(models.Model):
    name = models.CharField(max_length=100)
    max_bytes = models.PositiveIntegerField(default=5000)

    def __str__(self):
        return self.name

    def post(self, content, parent=None, user=None):
        new_size = len(content.encode('utf-8'))
        self._cleanup_if_needed(extra_bytes=new_size)
        return Message.objects.create(room=self, content=content, parent=parent, user=user)

    @property
    def bytes_used(self):
        return sum(len(msg.content.encode('utf-8')) for msg in self.messages.all())

    def _cleanup_if_needed(self, extra_bytes=0):
        messages = list(self.messages.order_by('timestamp'))
        total_size = sum(m.size_in_bytes() for m in messages)

        while total_size + extra_bytes > self.max_bytes and messages:
            oldest = messages[0]  # ✅ Use list indexing
            total_size -= oldest.size_in_bytes()
            oldest.delete()
            messages = messages[1:]  # ✅ Still a list, safe to slice

    def save(self, *args, **kwargs):
        if self.pk:
            old = Room.objects.get(pk=self.pk)
            if self.max_bytes < old.max_bytes:
                self._cleanup_if_needed()
        super().save(*args, **kwargs)


class Message(models.Model):
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='messages')
    content = models.TextField()
    timestamp = models.DateTimeField(default=timezone.now)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, related_name='replies')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='messages')

    def __str__(self):
        return f"{self.timestamp} - {self.content[:30]}"

    def size_in_bytes(self):
        return len(self.content.encode('utf-8'))

    def is_root(self):
        return self.parent is None
