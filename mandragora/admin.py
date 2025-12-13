from django.contrib import admin
from django import forms
from django_ace import AceWidget
from .models import Workflow, LambdaNode, Edge
from django_json_widget.widgets import JSONEditorWidget

# --- Workflow Runner (agnostic) ---
def run_workflow(workflow, context=None):
    results = []
    node = workflow.nodes.filter(is_initial=True).first()
    context = context or {}

    while node:
        result = node.execute(context)
        results.append({"node": node.name, "result": result})
        context = {**context, **result}

        if node.is_final:
            break

        next_edge = node.outgoing_edges.first()
        if next_edge:
            node = next_edge.target
        else:
            break

    return results


# --- Admin Actions ---
@admin.action(description="Run selected workflows")
def run_selected_workflows(modeladmin, request, queryset):
    for workflow in queryset:
        results = run_workflow(workflow, context={"request_user": request.user.username})
        modeladmin.message_user(
            request,
            f"Workflow '{workflow.name}' executed. Results: {results}"
        )


@admin.action(description="Test selected LambdaNodes")
def test_selected_nodes(modeladmin, request, queryset):
    """
    Admin action: execute each node using its stored test_context JSON.
    """
    for node in queryset:
        result = node.execute(node.test_context)
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
            "code": AceWidget(mode="python", theme="chrome", width="100%", height="400px"),
            #"test_context": JSONEditorWidget(),
        }


# --- Admin Classes ---
@admin.register(Workflow)
class WorkflowAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "timeout")
    actions = [run_selected_workflows]


@admin.register(LambdaNode)
class LambdaNodeAdmin(admin.ModelAdmin):
    form = LambdaNodeForm
    list_display = ("name", "workflow", "is_initial", "is_final")
    list_filter = ("workflow", "is_initial", "is_final")
    actions = [test_selected_nodes]  # attach the test action


@admin.register(Edge)
class EdgeAdmin(admin.ModelAdmin):
    list_display = ("workflow", "source", "target", "action")
    list_filter = ("workflow",)
