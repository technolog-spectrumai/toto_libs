from django.db import models
from django.utils.text import slugify

from toto.core.domain import DomainEntity


class Category(DomainEntity):
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class IdeaBox(DomainEntity):
    title = models.CharField(max_length=160, blank=True)
    body = models.TextField(blank=True)

    is_concept = models.BooleanField(
        default=False,
        help_text="Marks this box as a concept/tag-like node rather than a normal captured idea.",
    )

    source_title = models.CharField(max_length=255, blank=True)
    source_url = models.URLField(blank=True)
    source_type = models.CharField(
        max_length=40,
        blank=True,
        help_text="Book, article, movie, video, podcast, conversation, etc.",
    )

    quote = models.TextField(blank=True)

    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        related_name="idea_boxes",
        null=True,
        blank=True,
    )

    properties = models.JSONField(
        default=dict,
        blank=True,
        help_text="Flexible metadata, e.g. {'topic': 'memory', 'rating': 4, 'status': 'raw'}.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title or self.body[:80] or "Untitled box"

    @property
    def is_note(self):
        return not self.is_concept

    def get_property(self, key, default=None):
        return self.properties.get(key, default)

    def set_property(self, key, value):
        self.properties[key] = value
        self.save(update_fields=["properties", "updated_at"])


class IdeaLink(DomainEntity):
    from_box = models.ForeignKey(
        IdeaBox,
        on_delete=models.CASCADE,
        related_name="outgoing_links",
    )

    to_box = models.ForeignKey(
        IdeaBox,
        on_delete=models.CASCADE,
        related_name="incoming_links",
    )

    label = models.CharField(
        max_length=80,
        blank=True,
        help_text="Free-text relationship label, e.g. about, supports, contradicts, expands.",
    )

    properties = models.JSONField(
        default=dict,
        blank=True,
        help_text="Flexible metadata for the link.",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("from_box", "to_box", "label")]
        ordering = ["-created_at"]

    def __str__(self):
        label = self.label or "related to"
        return f"{self.from_box} → {label} → {self.to_box}"
