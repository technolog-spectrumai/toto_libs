from django.contrib import admin
from .models import Workflow, LambdaNode, Edge
from django_ace import AceWidget # TODO: use it for python code editing
from django import forms

# --- Workflow Runner (agnostic) ---
def run_workflow(workflow, context=None):
    """
    Executes all LambdaNodes in a workflow sequentially.
    Starts at the initial node and follows edges until final.
    """
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
    """
    Admin action to run all selected workflows.
    Displays results in the admin messages.
    """
    for workflow in queryset:
        results = run_workflow(workflow, context={"request_user": request.user.username})
        modeladmin.message_user(
            request,
            f"Workflow '{workflow.name}' executed. Results: {results}"
        )


# --- Admin Classes ---
@admin.register(Workflow)
class WorkflowAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "timeout")
    actions = [run_selected_workflows]


class LambdaNodeForm(forms.ModelForm):
    class Meta:
        model = LambdaNode
        fields = "__all__"
        widgets = {
            "code": AceWidget(mode="python", theme="chrome"),
        }

@admin.register(LambdaNode)
class LambdaNodeAdmin(admin.ModelAdmin):
    form = LambdaNodeForm
    list_display = ("name", "workflow", "is_initial", "is_final")
    list_filter = ("workflow", "is_initial", "is_final")


@admin.register(Edge)
class EdgeAdmin(admin.ModelAdmin):
    list_display = ("workflow", "source", "target", "action")
    list_filter = ("workflow",)
