from django.db import models
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.utils.text import slugify
import jsonschema
import uuid
from django.template.loader import render_to_string


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


class PageGenerator(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True, blank=True)
    html_template = models.TextField()
    json_schema = models.JSONField()

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class DynamicPage(BasePage):
    generator = models.ForeignKey(PageGenerator, on_delete=models.PROTECT)
    config_json = models.JSONField()

    def clean(self):
        """Validate config_json against the generator's schema."""
        try:
            jsonschema.validate(instance=self.config_json, schema=self.generator.json_schema)
        except jsonschema.ValidationError as e:
            raise ValidationError({'config_json': _(str(e))})

    def render_to_string(self):
        return render_to_string(
            template_name=self.generator.html_template,
            context=self.config_json
        )


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