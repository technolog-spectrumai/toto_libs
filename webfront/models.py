from django.db import models
from django.urls import reverse
import jsonschema
from django.core.exceptions import ValidationError


class HtmlTemplate(models.Model):
    name = models.CharField(max_length=255, unique=True)
    content = models.TextField(help_text="Raw HTML template content")
    created_at = models.DateTimeField(auto_now_add=True)
    json_schema = models.JSONField(
        blank=True,
        null=True,
        help_text="Optional JSON Schema to validate DynamicPage data"
    )

    class Meta:
        verbose_name = "HTML Template"
        verbose_name_plural = "HTML Templates"

    def __str__(self):
        return self.name


class StaticPage(models.Model):
    slug = models.SlugField(max_length=255, unique=True)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Static Page"
        verbose_name_plural = "Static Pages"

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("webfront:static_page_detail", args=[self.slug])


class DynamicPage(models.Model):
    slug = models.SlugField(max_length=255, unique=True)
    title = models.CharField(max_length=255)
    data = models.JSONField(help_text="JSON data to be injected into template")
    template = models.ForeignKey(HtmlTemplate, on_delete=models.CASCADE, related_name="pages")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Dynamic Page"
        verbose_name_plural = "Dynamic Pages"

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("webfront:dynamic_page_detail", args=[self.slug])

    def clean(self):
        """
        Validate data against template's JSON schema if provided.
        """
        if self.template and self.template.json_schema:
            try:
                jsonschema.validate(instance=self.data, schema=self.template.json_schema)
            except jsonschema.ValidationError as e:
                raise ValidationError({"data": f"Invalid data for template '{self.template.name}': {e.message}"})
