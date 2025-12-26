from django.contrib import admin, messages
from django import forms

from django_json_widget.widgets import JSONEditorWidget
from django_ace import AceWidget

from .models import DynamicPage, PageWidget


# ---------------------------------------------------------
# Lambda Execution Helper (kept for test action)
# ---------------------------------------------------------

def execute_lambda(code: str, context: dict):
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
# Forms
# ---------------------------------------------------------

class DynamicPageForm(forms.ModelForm):
    class Meta:
        model = DynamicPage
        fields = "__all__"


class PageWidgetForm(forms.ModelForm):
    class Meta:
        model = PageWidget
        fields = "__all__"
        widgets = {
            "config": JSONEditorWidget(),
            "test_context": JSONEditorWidget(),
            "code": AceWidget(mode="python", theme="chrome", width="100%", height="300px"),
        }


# ---------------------------------------------------------
# Inline Widget Admin (kept EXACTLY as you requested)
# ---------------------------------------------------------

class PageWidgetInline(admin.StackedInline):
    model = PageWidget
    form = PageWidgetForm
    extra = 0
    show_change_link = True

    fields = (
        "name",
        "widget_type",
        "config",
        "code"
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
            f"Widget '{widget.name}' executed. Result: {result}"
        )


# ---------------------------------------------------------
# DynamicPage Admin
# ---------------------------------------------------------

@admin.register(DynamicPage)
class DynamicPageAdmin(admin.ModelAdmin):
    form = DynamicPageForm

    list_display = ("name", "slug", "created_by", "created_at")
    search_fields = ("name", "slug")

    inlines = [PageWidgetInline]

    readonly_fields = ("slug", "created_by")

    fieldsets = (
        ("Page Info", {
            "fields": ("name", "slug", "description", "created_by")
        }),
    )

    def save_model(self, request, obj, form, change):
        # Auto-assign created_by only on creation
        if not obj.pk:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


# ---------------------------------------------------------
# PageWidget Admin
# ---------------------------------------------------------

@admin.register(PageWidget)
class PageWidgetAdmin(admin.ModelAdmin):
    form = PageWidgetForm

    list_display = ("name", "widget_type", "page", "updated_at")
    list_filter = ("widget_type", "page")
    search_fields = ("name", "widget_type")

    actions = [test_selected_widgets]

    fieldsets = (
        ("Widget", {
            "fields": ("page", "name", "widget_type")
        }),
        ("Configuration", {
            "fields": ("config",)
        }),
        ("Lambda", {
            "fields": ("code", "test_context")
        }),
    )
