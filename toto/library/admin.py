from django.contrib import admin
from django.utils.html import format_html

from .models import Book, Article


class LibraryItemAdmin(admin.ModelAdmin):
    list_display = ["title", "author_display", "year", "vault_link"]
    search_fields = ["title", "abstract", "doi"]
    list_filter = ["year", "tags"]
    filter_horizontal = ["authors", "tags"]
    prepopulated_fields = {"slug": ("title",)}

    def author_display(self, obj):
        return obj.author_display()
    author_display.short_description = "Authors"

    def vault_link(self, obj):
        if obj.vault_file:
            url = obj.vault_file.get_public_url()
            if url:
                return format_html('<a href="{}" target="_blank">🔗 File</a>', url)
        return "—"
    vault_link.short_description = "Vault"


@admin.register(Book)
class BookAdmin(LibraryItemAdmin):
    fieldsets = [
        (None, {"fields": ["title", "slug", "authors", "year", "abstract", "tags", "vault_file", "url", "doi"]}),
        ("Publication", {"fields": ["publisher", "edition", "isbn"]}),
    ]


@admin.register(Article)
class ArticleAdmin(LibraryItemAdmin):
    fieldsets = [
        (None, {"fields": ["title", "slug", "authors", "year", "abstract", "tags", "vault_file", "url", "doi"]}),
        ("Journal", {"fields": ["journal", "volume", "issue", "pages"]}),
    ]
