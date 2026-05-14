from django.contrib import admin

from .models import Category, IdeaBox, IdeaLink


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "description")
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}


class OutgoingIdeaLinkInline(admin.TabularInline):
    model = IdeaLink
    fk_name = "from_box"
    extra = 1
    autocomplete_fields = ["to_box"]
    fields = ("label", "to_box", "properties", "created_at")
    readonly_fields = ("created_at",)


class IncomingIdeaLinkInline(admin.TabularInline):
    model = IdeaLink
    fk_name = "to_box"
    extra = 0
    autocomplete_fields = ["from_box"]
    fields = ("label", "from_box", "properties", "created_at")
    readonly_fields = ("label", "from_box", "properties", "created_at")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(IdeaBox)
class IdeaBoxAdmin(admin.ModelAdmin):
    list_display = (
        "display_name",
        "is_concept",
        "category",
        "source_type",
        "source_title",
        "created_at",
        "updated_at",
    )
    list_filter = ("is_concept", "category", "source_type", "created_at", "updated_at")
    search_fields = (
        "title",
        "body",
        "quote",
        "source_title",
        "source_url",
        "source_type",
        "category__name",
        "category__slug",
    )
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("title", "body", "is_concept", "category")}),
        (
            "Source",
            {
                "fields": ("source_title", "source_url", "source_type", "quote"),
                "classes": ("collapse",),
            },
        ),
        ("Properties", {"fields": ("properties",)}),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )
    inlines = [OutgoingIdeaLinkInline, IncomingIdeaLinkInline]

    def display_name(self, obj):
        return str(obj)[:100]

    display_name.short_description = "IdeaBox"


@admin.register(IdeaLink)
class IdeaLinkAdmin(admin.ModelAdmin):
    list_display = ("from_box", "label", "to_box", "created_at")
    list_filter = ("label", "created_at")
    search_fields = (
        "from_box__title",
        "from_box__body",
        "to_box__title",
        "to_box__body",
        "label",
    )
    autocomplete_fields = ("from_box", "to_box")
    readonly_fields = ("created_at",)
    fieldsets = (
        (None, {"fields": ("from_box", "label", "to_box")}),
        ("Properties", {"fields": ("properties",)}),
        ("Timestamps", {"fields": ("created_at",), "classes": ("collapse",)}),
    )