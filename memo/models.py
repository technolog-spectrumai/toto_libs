from django.db import models
from django.contrib.auth.models import User
from django_jsonform.models.fields import JSONField
from django.forms.models import model_to_dict


class Tag(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name


class MermaidChart(models.Model):
    title = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    code = models.TextField(blank=True)

    def __str__(self):
        return self.title or "Mermaid Chart"

    def to_json(self):
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "code": self.code,
        }


class MemoDeck(models.Model):
    title = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='decks')
    created_at = models.DateTimeField(auto_now_add=True)
    tags = models.ManyToManyField(Tag, related_name='decks', blank=True)


    def __str__(self):
        return self.title

    def to_json(self):
        return {
            "title": self.title,
            "description": self.description,
            "author": self.author.username,
            "created_at": self.created_at.isoformat(),
            "tags": [tag.name for tag in self.tags.all()],
            "cards": [card.to_json() for card in self.cards.all()],
        }


class MemoCard(models.Model):
    deck = models.ForeignKey(MemoDeck, on_delete=models.CASCADE, related_name='cards')
    title = models.CharField(max_length=200)
    content = models.TextField()
    order = models.PositiveIntegerField(default=0)
    image = models.ImageField(upload_to='card_images/', blank=True, null=True)
    chart = models.ForeignKey(
        MermaidChart,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cards'
    )

    class Meta:
        ordering = ['order']  # Ensures cards are always sorted by order

    def __str__(self):
        return f"{self.title} ({self.deck.title})"

    @property
    def image_url(self):
        if self.image and hasattr(self.image, 'url'):
            return self.image.url
        return None

    def save(self, *args, **kwargs):
        if self.pk:
            old = MemoCard.objects.get(pk=self.pk)
            if old.image and old.image != self.image:
                old.image.delete(save=False)
        super().save(*args, **kwargs)

    def to_json(self):
        data = model_to_dict(self, fields=["id", "title", "content", "order"])
        data["image"] = self.image_url
        data["deck"] = self.deck.title
        return data