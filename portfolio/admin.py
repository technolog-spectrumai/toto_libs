from django.contrib import admin
from .models import Company, FractionalOwnership


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "registration_number", "email", "founded_date", "updated_at")
    search_fields = ("name", "registration_number", "email")
    list_filter = ("founded_date",)
    readonly_fields = ("created_at", "updated_at")
    prepopulated_fields = {"slug": ("name",)}

    fieldsets = (
        ("Basic Info", {
            "fields": ("name", "slug", "registration_number", "founded_date")
        }),
        ("Contact", {
            "fields": ("email", "website", "headquarters")
        }),
        ("Metadata", {
            "fields": ("metadata",)
        }),
        ("System", {
            "fields": ("created_at", "updated_at"),
        }),
    )


@admin.register(FractionalOwnership)
class FractionalOwnershipAdmin(admin.ModelAdmin):
    list_display = ("owner_entity", "percentage", "created_at")
    search_fields = (
        "owner_entity__id",
        "owner_entity__polymorphic_ctype__model",
    )
    readonly_fields = ("created_at",)
