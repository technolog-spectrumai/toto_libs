from django.contrib import admin
from django import forms
from django_ace import AceWidget
from django_json_widget.widgets import JSONEditorWidget

from .models import LambdaNode, LambdaLayer, LambdaUnitTest


# --- Admin Actions ---
@admin.action(description="Run selected LambdaNodes")
def test_selected_nodes(modeladmin, request, queryset):
    """
    Admin action: execute each node with empty context.
    (Since test_context no longer exists)
    """
    for node in queryset:
        result = node.execute({})
        modeladmin.message_user(
            request,
            f"Node '{node.name}' executed. Result: {result}"
        )


# --- Forms ---
class LambdaNodeForm(forms.ModelForm):
    class Meta:
        model = LambdaNode
        fields = "__all__"
        widgets = {
            "code": AceWidget(
                mode="python",
                theme="chrome",
                width="100%",
                height="400px"
            ),
        }


class LambdaUnitTestForm(forms.ModelForm):
    class Meta:
        model = LambdaUnitTest
        fields = "__all__"
        widgets = {
            "input_context": JSONEditorWidget(),
            "expected_output": JSONEditorWidget(),
            "actual_output": JSONEditorWidget(),
        }


# --- Admin Classes ---
@admin.register(LambdaNode)
class LambdaNodeAdmin(admin.ModelAdmin):
    form = LambdaNodeForm
    list_display = ("name", "layer")
    list_filter = ("layer",)
    actions = [test_selected_nodes]


@admin.register(LambdaLayer)
class LambdaLayerAdmin(admin.ModelAdmin):
    list_display = ("name", "use_opencv")
    search_fields = ("name",)


@admin.register(LambdaUnitTest)
class LambdaUnitTestAdmin(admin.ModelAdmin):
    form = LambdaUnitTestForm
    list_display = ("name", "node", "passed", "executed_at")
    list_filter = ("passed", "node")
    readonly_fields = ("actual_output", "passed", "executed_at")

    @admin.action(description="Run selected unit tests")
    def run_tests(self, request, queryset):
        for test in queryset:
            result = test.run()
            self.message_user(
                request,
                f"Test '{test.name}' executed. Passed={result['passed']}"
            )

    actions = [run_tests]
