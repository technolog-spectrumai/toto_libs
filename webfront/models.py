from django.db import models
from django.urls import reverse

class Page(models.Model):
    slug = models.SlugField(max_length=255, unique=True)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)   # main content fragment

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Page"
        verbose_name_plural = "Pages"

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("webfront:page_detail", args=[self.slug])
