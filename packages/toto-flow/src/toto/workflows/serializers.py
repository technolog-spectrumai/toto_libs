from rest_framework import serializers

from .models import (
    Report,
    ReportPage,
    ReportTemplate,
    Workflow,
    WorkflowEdge,
    WorkflowEdgeRun,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowRun,
)


class ReportTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportTemplate
        fields = [
            "id", "name", "slug", "report_type", "description",
            "definition", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ReportPageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportPage
        fields = ["id", "report", "key", "title", "order", "blocks", "data", "created_at"]
        read_only_fields = ["id", "created_at"]


class ReportSerializer(serializers.ModelSerializer):
    pages = ReportPageSerializer(many=True, read_only=True)

    class Meta:
        model = Report
        fields = [
            "id", "template", "workflow_run", "source_node_run", "title", "slug",
            "report_type", "status", "definition", "data", "metadata",
            "created_at", "updated_at", "pages",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "pages"]


class WorkflowNodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowNode
        fields = [
            "id", "workflow", "node_type", "label",
            "lambda_function", "report_template",
            "config", "position_x", "position_y",
        ]
        read_only_fields = ["id"]


class WorkflowEdgeSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowEdge
        fields = ["id", "workflow", "source", "target", "branch_key", "is_default"]
        read_only_fields = ["id"]

    def validate(self, attrs):
        if attrs.get("source") == attrs.get("target"):
            raise serializers.ValidationError("source and target must be different nodes.")
        if attrs.get("source") and attrs.get("target"):
            source_wf = attrs["source"].workflow_id
            target_wf = attrs["target"].workflow_id
            if source_wf != target_wf:
                raise serializers.ValidationError(
                    "source and target must belong to the same workflow."
                )
        return attrs


class WorkflowSerializer(serializers.ModelSerializer):
    nodes = WorkflowNodeSerializer(many=True, read_only=True)
    edges = WorkflowEdgeSerializer(many=True, read_only=True)

    class Meta:
        model = Workflow
        fields = ["id", "name", "slug", "description", "created_at", "nodes", "edges"]
        read_only_fields = ["id", "slug", "created_at"]


class WorkflowListSerializer(serializers.ModelSerializer):
    node_count = serializers.IntegerField(source="nodes.count", read_only=True)

    class Meta:
        model = Workflow
        fields = ["id", "name", "slug", "description", "created_at", "node_count"]
        read_only_fields = ["id", "slug", "created_at"]


class WorkflowNodeRunSerializer(serializers.ModelSerializer):
    node_type = serializers.CharField(source="node.node_type", read_only=True)
    node_label = serializers.CharField(source="node.label", read_only=True)

    class Meta:
        model = WorkflowNodeRun
        fields = [
            "id", "node", "node_type", "node_label", "status",
            "input_data", "output_data", "error", "celery_task_id",
            "started_at", "completed_at",
        ]
        read_only_fields = fields


class WorkflowEdgeRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowEdgeRun
        fields = ["id", "edge", "activated", "activated_at"]
        read_only_fields = fields


class WorkflowRunSerializer(serializers.ModelSerializer):
    node_runs = WorkflowNodeRunSerializer(many=True, read_only=True)
    edge_runs = WorkflowEdgeRunSerializer(many=True, read_only=True)

    class Meta:
        model = WorkflowRun
        fields = [
            "id", "workflow", "status", "input_data", "output_data",
            "started_at", "completed_at", "created_at",
            "node_runs", "edge_runs",
        ]
        read_only_fields = [
            "id", "status", "output_data",
            "started_at", "completed_at", "created_at",
            "node_runs", "edge_runs",
        ]


class StartRunSerializer(serializers.Serializer):
    input_data = serializers.JSONField(default=dict, required=False)
