from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify
import uuid


class Template(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)
    header = models.TextField(
        blank=True,
        null=True,
        help_text="HTML or Django template code for the <head> section"
    )
    content = models.TextField(
        help_text="Django template body content with {{ variables }}"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name



class Image(models.Model):
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='images')
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    image = models.ImageField(upload_to='uploads/images/')
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)
            self.slug = f"{base}-{uuid.uuid4().hex[:6]}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Page(models.Model):
    template = models.ForeignKey(Template, on_delete=models.CASCADE, related_name='pages')
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='pages')
    data = models.JSONField(help_text="Context for rendering the template")
    language = models.CharField(max_length=20, default='en')
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(f"{self.template.name}-{uuid.uuid4()}")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.slug} by {self.author.username}"


