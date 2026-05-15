from django.contrib import admin
from .models import LatexWorkspace, LatexFile


class LatexFileInline(admin.TabularInline):
    model = LatexFile
    extra = 0
    fields = ("vault_file", "file_type", "added_at")
    readonly_fields = ("added_at",)


@admin.register(LatexWorkspace)
class LatexWorkspaceAdmin(admin.ModelAdmin):
    list_display = ("name", "bucket", "owner", "created_at")
    list_filter = ("bucket",)
    search_fields = ("name", "slug", "bucket__owner__username")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [LatexFileInline]

    def owner(self, obj):
        return obj.bucket.owner
    owner.admin_order_field = "bucket__owner"
    owner.short_description = "Owner"


@admin.register(LatexFile)
class LatexFileAdmin(admin.ModelAdmin):
    list_display = ("vault_file", "file_type", "workspace", "added_at")
    list_filter = ("file_type", "workspace")
    search_fields = ("vault_file__title", "workspace__name")
    readonly_fields = ("added_at",)
