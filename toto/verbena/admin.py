from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from django import forms
from markdownx.widgets import MarkdownxWidget
from .models import Page, Section, Subsection, Image, Tag, Topic


# ────────────────────────────────────────────────
# FORMS
# ────────────────────────────────────────────────

class SectionAdminForm(forms.ModelForm):
    class Meta:
        model = Section
        fields = "__all__"
        widgets = {
            "content": MarkdownxWidget(),
        }


class SectionInlineForm(forms.ModelForm):
    class Meta:
        model = Section
        fields = "__all__"
        widgets = {
            "content": MarkdownxWidget(),
        }


class SubsectionInlineForm(forms.ModelForm):
    class Meta:
        model = Subsection
        fields = "__all__"
        widgets = {
            "content": MarkdownxWidget(),
        }


# ────────────────────────────────────────────────
# INLINES
# ────────────────────────────────────────────────

class SubsectionInline(admin.StackedInline):
    model = Subsection
    form = SubsectionInlineForm
    extra = 1
    fields = ["title", "content", "image", "order", "topics"]
    ordering = ["order"]
    show_change_link = True


class SectionInline(admin.StackedInline):
    model = Section
    form = SectionInlineForm
    extra = 1
    fields = ["title", "content", "author", "order", "topics"]
    ordering = ["order"]
    show_change_link = True


# ────────────────────────────────────────────────
# ADMINS
# ────────────────────────────────────────────────

@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    list_display = ["title", "created_at", "author_list", "view_page"]
    search_fields = ["title", "description"]
    list_filter = ["tags"]
    prepopulated_fields = {"slug": ("title",)}
    filter_horizontal = ["tags"]
    inlines = [SectionInline]

    def author_list(self, obj):
        authors = obj.authors()
        return ", ".join(a.full_name for a in authors) if authors else "—"
    author_list.short_description = "Authors"

    def view_page(self, obj):
        url = reverse("verbena:page_detail", kwargs={"slug": obj.slug})
        return format_html('<a href="{}" target="_blank">🔗 View</a>', url)
    view_page.short_description = "Page URL"


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    form = SectionAdminForm
    list_display = ["title", "page", "order", "author"]
    list_filter = ["page", "author"]
    ordering = ["page", "order"]
    inlines = [SubsectionInline]
    filter_horizontal = ["tags", "topics"]


@admin.register(Subsection)
class SubsectionAdmin(admin.ModelAdmin):
    list_display = ["title", "section", "order", "image"]
    list_filter = ["section", "topics"]
    ordering = ["section", "order"]
    filter_horizontal = ["topics"]


@admin.register(Image)
class ImageAdmin(admin.ModelAdmin):
    list_display = ["title", "preview"]
    search_fields = ["title"]

    def preview(self, obj):
        if obj.file:
            return format_html(
                '<img src="{}" style="height:40px;border-radius:4px;" />',
                obj.file.url
            )
        return "—"
    preview.short_description = "Preview"


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ["name", "slug"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ["name"]
    search_fields = ["name", "description"]
    list_filter = ["community", "person", "event", "route", "territory", "address", "federation"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["linked_entities"]

    def linked_entities(self, obj):
        parts = []
        if obj.community:
            parts.append(f"Community: {obj.community}")
        if obj.person:
            parts.append(f"Person: {obj.person}")
        if obj.event:
            parts.append(f"Event: {obj.event}")
        if obj.route:
            parts.append(f"Route: {obj.route}")
        if obj.territory:
            parts.append(f"Territory: {obj.territory}")
        if obj.address:
            parts.append(f"Address: {obj.address}")
        if obj.federation:
            parts.append(f"Federation: {obj.federation}")
        return ",\n".join(parts) if parts else "—"

    linked_entities.short_description = "Linked Entities"