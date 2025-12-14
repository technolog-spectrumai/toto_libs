from django.db import models
from django.urls import reverse
import jsonschema
from django.core.exceptions import ValidationError
from mandragora.models import LambdaNode


class HtmlTemplate(models.Model):
    name = models.CharField(max_length=255, unique=True)
    content = models.TextField(help_text="Raw HTML template content")
    created_at = models.DateTimeField(auto_now_add=True)
    json_schema = models.JSONField(
        blank=True,
        null=True,
        help_text="Optional JSON Schema to validate DynamicPage data"
    )
    script = models.TextField(
        blank=True,
        null=True,
        help_text="Optional JavaScript to be injected into the page"
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
    lambda_node = models.ForeignKey(
        LambdaNode,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="dynamic_pages",
        help_text="Optional LambdaNode to process context before rendering"
    )

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


class Chart(models.Model):
    CHART_TYPES = [
        ('line', 'Line'),
        ('bar', 'Bar'),
        ('pie', 'Pie'),
        ('doughnut', 'Doughnut'),
    ]

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    chart_type = models.CharField(max_length=20, choices=CHART_TYPES)
    stacked = models.BooleanField(default=False)

    # Optional LambdaNode to compute chart data
    lambda_node = models.ForeignKey(
        LambdaNode,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="charts",
        help_text="Optional LambdaNode to compute chart data"
    )

    # Optional JSON parameters for the chart
    params = models.JSONField(
        default=dict,
        blank=True,
        help_text="Optional JSON parameters passed to LambdaNode when computing data"
    )

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title

    def compute_data(self, extra_context=None):
        """
        Execute linked LambdaNode to get chart data.
        Expected return: {"labels": [...], "datasets": [...]}
        """
        if self.lambda_node:
            context = {**self.params, **(extra_context or {})}
            result = self.lambda_node.execute(context)
            if isinstance(result, dict):
                return {
                    "labels": result.get("labels", []),
                    "datasets": result.get("datasets", [])
                }
        return {"labels": [], "datasets": []}


# ────────────────────────────────────────────────
# 📑 Metrics Page Model
# ────────────────────────────────────────────────

class MetricsPage(models.Model):
    slug = models.SlugField(max_length=255, unique=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    charts = models.ManyToManyField(
        Chart,
        related_name="metrics_pages",
        blank=True,
        help_text="Charts included in this metrics page"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "title"]
        verbose_name = "Metrics Page"
        verbose_name_plural = "Metrics Pages"

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("webfront:metrics_page_detail", args=[self.slug])

    def get_chart_data(self, context=None):
        """
        Collect chart data for all charts in this page.
        """
        data = []
        for chart in self.charts.all():
            chart_data = chart.compute_data(extra_context=context)
            data.append({
                "id": f"chart_{chart.pk}",
                "title": chart.title,
                "description": chart.description,
                "type": chart.chart_type,
                "stacked": chart.stacked,
                "labels": chart_data["labels"],
                "datasets": chart_data["datasets"],
            })
        return data
