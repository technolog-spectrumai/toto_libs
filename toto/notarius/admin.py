from django.contrib import admin

from toto.notarius.models import ContractTemplate


@admin.register(ContractTemplate)
class ContractTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "key", "is_default", "updated_at")
    list_filter = ("is_default",)
    search_fields = ("name", "key", "description")
    prepopulated_fields = {"key": ("name",)}
    fields = ("name", "key", "description", "is_default", "latex_source")
