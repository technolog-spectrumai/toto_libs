from django.contrib import admin

from .models import Contract, ContractNode, ContractEdge, ContractSignatory


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


class ContractSignatoryInline(admin.TabularInline):
    model = ContractSignatory
    extra = 0
    fields = ("person", "is_required", "signed_at")
    readonly_fields = ("signed_at",)
    raw_id_fields = ("person",)


@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "uuid", "node_count", "edge_count", "created_at")
    list_filter = ("status",)
    search_fields = ("name", "description", "uuid")
    readonly_fields = ("uuid", "created_at", "updated_at", "executed_at")
    fields = ("name", "description", "status", "metadata", "uuid", "created_at", "updated_at", "executed_at")
    inlines = [ContractNodeInline, ContractEdgeInline, ContractSignatoryInline]

    @admin.display(description="Nodes")
    def node_count(self, obj):
        return obj.nodes.count()

    @admin.display(description="Edges")
    def edge_count(self, obj):
        return obj.edges.count()


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


@admin.register(ContractSignatory)
class ContractSignatoryAdmin(admin.ModelAdmin):
    list_display = ("contract", "person", "is_required", "has_signed", "signed_at", "added_at")
    list_filter = ("is_required",)
    search_fields = ("person__display_name", "contract__name")
    readonly_fields = ("signed_at", "added_at")
    raw_id_fields = ("contract", "person")

    @admin.display(boolean=True, description="Signed")
    def has_signed(self, obj):
        return obj.has_signed
