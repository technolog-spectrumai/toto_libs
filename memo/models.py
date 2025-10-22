from django.db import models
from django.contrib.auth.models import User
from django_jsonform.models.fields import JSONField


class Tag(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name


class MemoLatexPreset(models.Model):
    name = models.CharField(max_length=100, unique=True)
    include_title_page = models.BooleanField(default=True)
    include_table_of_contents = models.BooleanField(default=True)
    theme = models.CharField(max_length=100, default="Rochester")
    color_theme = models.CharField(max_length=100, default="seahorse")

    # Replace TextField with structured JSONField
    packages = JSONField(
        default=list,
        help_text="List of LaTeX package names, e.g. ['graphicx', 'amsmath']"
    )

    def __str__(self):
        return self.name


class MemoDeck(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='decks')
    created_at = models.DateTimeField(auto_now_add=True)
    tags = models.ManyToManyField(Tag, related_name='decks', blank=True)
    latex_preset = models.ForeignKey('MemoLatexPreset', on_delete=models.SET_NULL, null=True, blank=True, related_name='decks')

    def __str__(self):
        return self.title


class MemoCard(models.Model):
    deck = models.ForeignKey(MemoDeck, on_delete=models.CASCADE, related_name='cards')
    title = models.CharField(max_length=200)
    content = models.TextField()
    order = models.PositiveIntegerField(default=0)
    image = models.ImageField(upload_to='card_images/', blank=True, null=True)

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

