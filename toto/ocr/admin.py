from .models import (
    OcrProject,
    OcrImage,
    OcrLine
)
from django.contrib import admin
from .models import ImageTransform, ImageTransformParam
from django.utils.html import format_html
from django.urls import path, reverse
from .views import apply_transform_view

@admin.register(OcrProject)
class OcrProjectAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "slug", "bucket")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)
    list_filter = ("bucket",)


class OcrLineInline(admin.TabularInline):
    model = OcrLine
    extra = 0
    can_delete = False
    readonly_fields = ("text", "left", "top", "width", "height", "confidence")
    fields = ("text", "left", "top", "width", "height", "confidence")


# ---------------------------------------------------------
# OCR IMAGE ADMIN
# ---------------------------------------------------------

@admin.register(OcrImage)
class OcrImageAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "filename",
        "project",
        "processed",
        "uploaded_at",
        "processed_at",
    )

    list_filter = ("project", "processed", "language")
    search_fields = ("filename",)

    readonly_fields = (
        "filename",
        "file_size",
        "mime_type",
        "uploaded_at",
        "processed_at",
        "processed",
        "apply_transform_button",
        "extracted_text_display",
    )

    actions = ["action_run_ocr", "action_reset_ocr"]

    fieldsets = (
        ("Project", {"fields": ("project",)}),
        ("File Source", {"fields": ("image", "vault_file")}),
        ("Metadata", {"fields": ("filename", "file_size", "mime_type")}),
        ("Processing", {"fields": ("language", "processed", "processed_at", "extracted_text_display")}),
        ("Transform", {"fields": ("apply_transform_button",)}),
        ("Timestamps", {"fields": ("uploaded_at",)}),
    )

    inlines = [OcrLineInline]

    def extracted_text_display(self, obj):
        return obj.extracted_text
    extracted_text_display.short_description = "Extracted Text"

    # ---------------------------------------------------------
    # BASIC ACTIONS
    # ---------------------------------------------------------

    def action_run_ocr(self, request, queryset):
        for obj in queryset:
            obj.run_ocr()
        self.message_user(request, f"OCR processed for {queryset.count()} image(s).")

    def action_reset_ocr(self, request, queryset):
        for obj in queryset:
            obj.reset()
        self.message_user(request, f"OCR reset for {queryset.count()} image(s).")

    def apply_transform_button(self, obj):
        if not obj or not obj.id:
            return format_html("<em>Save to enable transforms</em>")

        url = reverse("admin:apply_transform", args=[obj.id])
        return format_html(f'<a class="button" href="{url}">Apply Transform</a>')

    apply_transform_button.short_description = "Transform"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "apply-transform/<int:image_id>/",
                self.admin_site.admin_view(apply_transform_view),
                name="apply_transform",
            )
        ]
        return custom + urls


# ---------------------------------------------------------
# OCR LINE ADMIN
# ---------------------------------------------------------

@admin.register(OcrLine)
class OcrLineAdmin(admin.ModelAdmin):
    list_display = ("id", "image", "text_preview", "left", "top", "width", "height", "confidence")
    readonly_fields = ("image", "text", "left", "top", "width", "height", "confidence")

    def text_preview(self, obj):
        return obj.text[:16] + "..." if len(obj.text) > 16 else obj.text
    text_preview.short_description = "Text"


class ImageTransformParamInline(admin.TabularInline):
    model = ImageTransformParam
    extra = 1
    fields = (
        "key",
        "min_value",
        "max_value",
        "default_value",
        "step",
        "description"
    )
    ordering = ("key",)


@admin.register(ImageTransform)
class ImageTransformAdmin(admin.ModelAdmin):
    list_display = ("name", "lambda_function", "created_at")
    search_fields = ("name", "lambda_function__function_name")
    inlines = [ImageTransformParamInline]
    readonly_fields = ("created_at",)
