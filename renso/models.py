from django.db import models
from django.contrib.auth.models import User

class InfoTag(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name


class MemoDeck(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='decks')
    created_at = models.DateTimeField(auto_now_add=True)
    tags = models.ManyToManyField(InfoTag, related_name='decks', blank=True)

    def __str__(self):
        return self.title


class MemoCard(models.Model):
    deck = models.ForeignKey(MemoDeck, on_delete=models.CASCADE, related_name='cards')
    title = models.CharField(max_length=200)
    content = models.TextField()
    mermaid_code = models.TextField(blank=True)

    def __str__(self):
        return f"{self.title} ({self.deck.title})"
