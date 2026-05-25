from django.contrib import admin
from django import forms as django_forms

from .models import Contract, ContractNode, ContractEdge
from .widgets import AceYamlWidget
from .services import sync_contract_code_snapshot


class _ContractAdminForm(django_forms.ModelForm):
    class Meta:
        model = Contract
        fields = "__all__"
        widgets = {"code": AceYamlWidget(attrs={"rows": 30})}


class ContractNodeInline(admin.TabularInline):
    model = ContractNode
    extra = 0
    fields = ("key", "node_type", "title", "is_manual", "object_app", "object_model", "object_id")
    show_change_link = True


class ContractEdgeInline(admin.TabularInline):
    model = ContractEdge
    extra = 0
    fields = ("source", "edge_type", "target", "label")
    show_change_link = True


@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    form = _ContractAdminForm
    list_display = ("name", "uuid", "node_count", "edge_count", "created_at")
    search_fields = ("name", "description", "uuid", "code")
    readonly_fields = ("uuid", "created_at", "updated_at")
    fields = ("name", "description", "code", "metadata", "uuid", "created_at", "updated_at")
    inlines = [ContractNodeInline, ContractEdgeInline]
    actions = ["action_sync_yaml_snapshot"]

    @admin.display(description="Nodes")
    def node_count(self, obj):
        return obj.nodes.count()

    @admin.display(description="Edges")
    def edge_count(self, obj):
        return obj.edges.count()

    @admin.action(description="Sync YAML snapshot from graph")
    def action_sync_yaml_snapshot(self, request, queryset):
        for contract in queryset:
            sync_contract_code_snapshot(contract)
        self.message_user(request, f"Synced YAML snapshot for {queryset.count()} contract(s).")


@admin.register(ContractNode)
class ContractNodeAdmin(admin.ModelAdmin):
    list_display = ("contract", "key", "node_type", "title", "is_manual", "object_app", "object_model", "object_id")
    list_filter = ("node_type", "is_manual", "object_app", "object_model")
    search_fields = ("key", "title", "description", "object_id")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("contract",)


@admin.register(ContractEdge)
class ContractEdgeAdmin(admin.ModelAdmin):
    list_display = ("contract", "source", "edge_type", "target", "label")
    list_filter = ("edge_type",)
    search_fields = ("label", "description", "source__key", "target__key")
    readonly_fields = ("created_at",)
    raw_id_fields = ("contract", "source", "target")
