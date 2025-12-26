from django.db import models


class DynamicPage(models.Model):
    """
    A Dynamic Page composed of widgets.
    The page may define a loader lambda that prepares shared context
    for all widgets before rendering.
    """

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    # Page-level loader lambda (optional)
    loader_code = models.TextField(
        blank=True,
        null=True,
        help_text="Restricted Python code that prepares shared context for all widgets on this page"
    )

    loader_test_context = models.JSONField(
        blank=True,
        null=True,
        help_text="Optional JSON context for testing the loader lambda"
    )

    # Optional global layout or metadata
    layout = models.JSONField(
        default=dict,
        help_text="Optional global layout or metadata for the dynamic page"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class PageWidget(models.Model):
    """
    A widget instance on a Dynamic Page.
    Each widget:
    - maps to a micro-frontend component via widget_type
    - has a JSON config
    - may define a transform lambda
    - has layout metadata for positioning
    """

    page = models.ForeignKey(
        DynamicPage,
        on_delete=models.CASCADE,
        related_name="widgets"
    )

    widget_type = models.CharField(
        max_length=100,
        help_text="Identifier for the widget micro-frontend (e.g. 'chart.line', 'table.basic')"
    )

    config = models.JSONField(
        default=dict,
        help_text="Widget-specific configuration passed to the micro-frontend"
    )

    code = models.TextField(
        blank=True,
        null=True,
        help_text="Restricted Python code snippet for transforming widget data"
    )

    test_context = models.JSONField(
        blank=True,
        null=True,
        help_text="Optional JSON context for testing the widget lambda"
    )

    layout = models.JSONField(
        default=dict,
        help_text="Layout information for the widget (x, y, w, h)"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.widget_type} on {self.page}"
