from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from toto.verbena.admin import PageAdminMixin, SectionInlineMixin, make_section_form

from .models import Page, Section, Tag


class SectionInline(SectionInlineMixin):
    model = Section
    fields = ["title", "content", "author", "order", "tags"]
    filter_horizontal = ["tags"]


@admin.register(Page)
class PageAdmin(PageAdminMixin):
    list_display = ["title", "created_at", "author_list", "reading_time", "view_page"]
    list_filter = ["tags"]
    filter_horizontal = ["tags"]
    inlines = [SectionInline]

    def author_list(self, obj):
        authors = obj.authors()
        return ", ".join(author.full_name for author in authors) if authors else "-"

    author_list.short_description = "Authors"

    def reading_time(self, obj):
        return f"{obj.reading_time_minutes} min"

    reading_time.short_description = "Read"

    def view_page(self, obj):
        url = reverse("palimpsest:page_detail", kwargs={"slug": obj.slug})
        return format_html('<a href="{}" target="_blank">View</a>', url)

    view_page.short_description = "URL"


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ["title", "page", "order", "author"]
    list_filter = ["page", "author", "tags"]
    ordering = ["page", "order"]
    filter_horizontal = ["tags"]

    def get_form(self, request, obj=None, **kwargs):
        kwargs.setdefault("form", make_section_form(Section))
        return super().get_form(request, obj, **kwargs)


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ["name", "slug"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ("name",)}
