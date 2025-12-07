from django.db import models
from django.urls import reverse


class HtmlTemplate(models.Model):
    name = models.CharField(max_length=255, unique=True)
    content = models.TextField(help_text="Raw HTML template content")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "HTML Template"
        verbose_name_plural = "HTML Templates"

    def __str__(self):
        return self.name


class StaticPage(models.Model):
    slug = models.SlugField(max_length=255, unique=True)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)   # main content fragment

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
