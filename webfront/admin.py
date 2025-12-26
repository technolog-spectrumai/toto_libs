from django.contrib import admin, messages
from django import forms
from django.utils.html import format_html

from django_json_widget.widgets import JSONEditorWidget
from django_ace import AceWidget

from .models import DynamicPage, PageWidget


# ---------------------------------------------------------
# Lambda Execution Helper
# ---------------------------------------------------------

def execute_lambda(code: str, context: dict):
    """
    Executes a lambda file's code safely by loading it into a namespace
    and calling main(ctx).
    """
    if not code:
        return None

    namespace = {}
    try:
        exec(code, namespace)
        if "main" not in namespace:
            return {"error": "No main(ctx) function defined"}
        return namespace["main"](context or {})
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------
# Custom Forms
# ---------------------------------------------------------

class DynamicPageForm(forms.ModelForm):
    class Meta:
        model = DynamicPage
        fields = "__all__"
        widgets = {
            "loader_code": AceWidget(mode="python", theme="chrome", width="100%", height="300px"),
            "loader_test_context": JSONEditorWidget(),
            "layout": JSONEditorWidget(),
        }


class PageWidgetForm(forms.ModelForm):
    class Meta:
        model = PageWidget
        fields = "__all__"
        widgets = {
            "config": JSONEditorWidget(),
            "test_context": JSONEditorWidget(),
            "layout": JSONEditorWidget(),
            "code": AceWidget(mode="python", theme="chrome", width="100%", height="300px"),
        }


# ---------------------------------------------------------
# Inline Widget Admin
# ---------------------------------------------------------

class PageWidgetInline(admin.StackedInline):
    model = PageWidget
    form = PageWidgetForm
    extra = 0
    show_change_link = True
    fields = (
        "widget_type",
        "config",
        "code",
    )

# ---------------------------------------------------------
# Admin Actions
# ---------------------------------------------------------

@admin.action(description="Run test on selected widgets")
def test_selected_widgets(modeladmin, request, queryset):
    for widget in queryset:
        result = execute_lambda(widget.code, widget.test_context)
        modeladmin.message_user(
            request,
            f"Widget '{widget.widget_type}' executed. Result: {result}"
        )


@admin.action(description="Run loader test on selected pages")
def test_selected_loaders(modeladmin, request, queryset):
    for page in queryset:
        result = execute_lambda(page.loader_code, page.loader_test_context)
        modeladmin.message_user(
            request,
            f"Loader for page '{page.name}' executed. Result: {result}"
        )


# ---------------------------------------------------------
# DynamicPage Admin
# ---------------------------------------------------------

@admin.register(DynamicPage)
class DynamicPageAdmin(admin.ModelAdmin):
    form = DynamicPageForm

    list_display = ("name", "updated_at", "loader_valid")
    search_fields = ("name",)

    inlines = [PageWidgetInline]

    readonly_fields = ("loader_valid",)

    fieldsets = (
        ("Page Info", {
            "fields": ("name", "description")
        }),
        ("Loader Lambda", {
            "fields": ("loader_code", "loader_test_context", "loader_valid")
        }),
        ("Layout", {
            "fields": ("layout",)
        }),
    )

    actions = [test_selected_loaders]

    def loader_valid(self, obj):
        if not obj.loader_code:
            return format_html('<span style="color: gray;">No loader</span>')
        if "def main" in obj.loader_code:
            return format_html('<span style="color: green; font-weight: bold;">Valid</span>')
        return format_html('<span style="color: red; font-weight: bold;">Invalid</span>')

    loader_valid.short_description = "Loader OK?"


# ---------------------------------------------------------
# PageWidget Admin
# ---------------------------------------------------------

@admin.register(PageWidget)
class PageWidgetAdmin(admin.ModelAdmin):
    form = PageWidgetForm

    list_display = ("widget_type", "page", "updated_at", "lambda_valid")
    list_filter = ("widget_type", "page")
    search_fields = ("widget_type",)

    readonly_fields = ("lambda_valid",)

    actions = [test_selected_widgets]

    fieldsets = (
        ("Widget", {
            "fields": ("page", "widget_type", "lambda_valid")
        }),
        ("Configuration", {
            "fields": ("config",)
        }),
        ("Lambda", {
            "fields": ("code", "test_context")
        })
    )

    def lambda_valid(self, obj):
        if not obj.code:
            return format_html('<span style="color: gray;">No code</span>')
        if "def main" in obj.code:
            return format_html('<span style="color: green; font-weight: bold;">Valid</span>')
        return format_html('<span style="color: red; font-weight: bold;">Invalid</span>')

    lambda_valid.short_description = "Lambda OK?"
