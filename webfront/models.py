from django.db import models
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.utils.text import slugify
from .validators import DataValidator
import uuid


class Language(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)

    def __str__(self):
        return self.name


class BasePage(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    language = models.ForeignKey(Language, on_delete=models.CASCADE)

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.name} ({self.language.slug})"


class StaticPage(BasePage):
    html = models.TextField()


class DynamicPage(BasePage):
    template_key = models.CharField(
        max_length=100,
        choices=[(key, DataValidator.AVAILABLE_TEMPLATES[key]['title']) for key in DataValidator.get_available_templates()]
    )
    config_json = models.JSONField()

    def clean(self):
        """Validate config_json against the selected template schema."""
        try:
            DataValidator.validate(self.template_key, self.config_json)
        except ValidationError as e:
            raise ValidationError({'config_json': _(str(e))})


class Image(models.Model):
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