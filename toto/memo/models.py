from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.forms.models import model_to_dict
from django.utils.text import slugify
from toto.vault.models import VaultFile


class Tag(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name


class MemoDiagram(models.Model):
    title = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    svg_file = models.ForeignKey(
        VaultFile,
        on_delete=models.PROTECT,
        related_name="memo_diagrams",
        limit_choices_to={"file_type": "svg"},
        help_text="SVG file stored in toto.vault."
    )

    def __str__(self):
        return self.title or self.svg_file.title or "Diagram"

    def clean(self):
        if self.svg_file and self.svg_file.file_type != "svg":
            raise ValidationError({"svg_file": "Memo diagrams must point to an SVG vault file."})

    @property
    def svg_url(self):
        return self.svg_file.get_public_url() if self.svg_file else None

    def to_json(self):
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "svg_url": self.svg_url,
            "vault_file": self.svg_file_id,
        }


class MemoDeck(models.Model):
    title = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='decks')
    created_at = models.DateTimeField(auto_now_add=True)
    tags = models.ManyToManyField(Tag, related_name='decks', blank=True)
    slug = models.SlugField(max_length=200, unique=True, blank=True)

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.title) or "deck"
            slug = base_slug
            counter = 1

            while MemoDeck.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1

            self.slug = slug

        super().save(*args, **kwargs)

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

    diagram = models.ForeignKey(
        MemoDiagram,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cards'
    )

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.title} ({self.deck.title})"

    @property
    def image_url(self):
        if self.image and hasattr(self.image, 'url'):
            return self.image.url
        return None

    def save(self, *args, **kwargs):
        # Delete old image if replaced
        if self.pk:
            old = MemoCard.objects.get(pk=self.pk)
            if old.image and old.image != self.image:
                old.image.delete(save=False)
        super().save(*args, **kwargs)

    def to_json(self):
        data = model_to_dict(self, fields=["id", "title", "content", "order"])
        data["image"] = self.image_url
        data["deck"] = self.deck.title

        if self.diagram:
            data["diagram"] = self.diagram.to_json()

        return data
