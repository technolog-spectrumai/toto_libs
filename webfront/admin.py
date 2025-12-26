from django.contrib import admin
from .models import DynamicPage, PageWidget


class PageWidgetInline(admin.StackedInline):
    model = PageWidget
    extra = 0
    show_change_link = True

    fields = (
        "widget_type",
        "config",
        "code",
        "test_context",
        "layout",
    )


@admin.register(DynamicPage)
class DynamicPageAdmin(admin.ModelAdmin):
    list_display = ("name", "updated_at")
    search_fields = ("name",)

    inlines = [PageWidgetInline]

    fields = (
        "name",
        "description",
        "loader_code",
        "loader_test_context",
        "layout",
    )


@admin.register(PageWidget)
class PageWidgetAdmin(admin.ModelAdmin):
    list_display = ("widget_type", "page", "updated_at")
    list_filter = ("widget_type", "page")
    search_fields = ("widget_type",)

    fields = (
        "page",
        "widget_type",
        "config",
        "code",
        "test_context",
        "layout",
    )
