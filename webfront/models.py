from django.db import models
from django.contrib.auth import get_user_model
from django.utils.text import slugify


User = get_user_model()


class DynamicPage(models.Model):
    """
    A Dynamic Page composed of widgets.
    """

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.TextField(blank=True)

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_pages",
        help_text="User who created this page"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        # Auto-generate slug if missing
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class PageWidget(models.Model):
    """
    A widget instance on a Dynamic Page.
    """

    name = models.CharField(
        max_length=200,
        help_text="Unique widget name within this page"
    )

    page = models.ForeignKey(
        DynamicPage,
        on_delete=models.CASCADE,
        related_name="widgets"
    )

    WIDGET_TYPES = [
        ("chart.line", "Line Chart"),
        ("chart.bar", "Bar Chart"),
        ("chart.pie", "Pie Chart"),
        ("table.basic", "Table"),
        ("card.basic", "Card"),
        ("list.basic", "List"),
    ]

    widget_type = models.CharField(max_length=100, choices=WIDGET_TYPES)
    config = models.JSONField(default=dict)
    code = models.TextField(blank=True, null=True)
    test_context = models.JSONField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("page", "name")
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.name} ({self.widget_type}) on {self.page}"
