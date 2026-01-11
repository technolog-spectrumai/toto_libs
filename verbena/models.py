from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify


class Page(models.Model):
    title = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, blank=True)
    description = models.TextField(blank=True)
    tags = models.CharField(max_length=255, blank=True)  # comma-separated tags

    author = models.ForeignKey(
        User,
        related_name="verbena_pages",
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def tag_list(self):
        return [t.strip() for t in self.tags.split(",") if t.strip()]

    def __str__(self):
        return self.title


class Section(models.Model):
    page = models.ForeignKey(
        Page,
        related_name="sections",
        on_delete=models.CASCADE
    )

    title = models.CharField(max_length=255, blank=True)
    content = models.TextField(blank=True)

    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.page.title} – {self.title or 'Section'}"


class Image(models.Model):
    section = models.ForeignKey(
        Section,
        related_name="images",
        on_delete=models.CASCADE
    )

    title = models.CharField(max_length=255, blank=True)
    file = models.ImageField(upload_to="verbena_images/")

    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return self.title or f"Image {self.id}"
from django.db import models

# Create your models here.
