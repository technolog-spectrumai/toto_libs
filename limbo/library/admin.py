import hashlib

from django.contrib import admin, messages
from django.core.files.base import ContentFile
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html

from .bibtex import collection_to_bibtex
from .forms import LibraryBibExportForm
from .models import Article, AudioReference, Book, LibraryCollection, VideoReference
from toto.vault.models import VaultFile


# ────────────────────────────────────────────────
# Shared base admin
# ────────────────────────────────────────────────

class LibraryItemAdmin(admin.ModelAdmin):
    list_display = ["title", "authors", "year", "vault_link"]
    search_fields = ["title", "authors", "abstract", "doi"]
    list_filter = ["year", "tags"]
    filter_horizontal = ["tags"]
    prepopulated_fields = {"slug": ("title",)}

    def vault_link(self, obj):
        if obj.vault_file:
            url = obj.vault_file.get_public_url()
            if url:
                return format_html('<a href="{}" target="_blank">File</a>', url)
        return "—"
    vault_link.short_description = "Vault"


# ────────────────────────────────────────────────
# Book / Article / Audio / Video
# ────────────────────────────────────────────────

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


@admin.register(AudioReference)
class AudioReferenceAdmin(LibraryItemAdmin):
    fieldsets = [
        (None, {"fields": ["title", "slug", "authors", "year", "abstract", "tags", "vault_file", "url", "doi"]}),
        ("Audio", {"fields": ["platform", "duration"]}),
    ]


@admin.register(VideoReference)
class VideoReferenceAdmin(LibraryItemAdmin):
    fieldsets = [
        (None, {"fields": ["title", "slug", "authors", "year", "abstract", "tags", "vault_file", "url", "doi"]}),
        ("Video", {"fields": ["platform", "director", "duration"]}),
    ]


# ────────────────────────────────────────────────
# Library Collection
# ────────────────────────────────────────────────

@admin.register(LibraryCollection)
class LibraryCollectionAdmin(admin.ModelAdmin):
    list_display = ["title", "slug", "item_count", "created_at"]
    search_fields = ["title", "description"]
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ["created_at"]
    filter_horizontal = ["books", "articles", "audio", "videos"]
    actions = ["bib_export_console_action"]

    def get_urls(self):
        return [
            path(
                "bib-export-console/<int:collection_id>/",
                self.admin_site.admin_view(self.bib_export_console_view),
                name="library_librarycollection_bib_export_console",
            ),
        ] + super().get_urls()

    def item_count(self, obj):
        return (
            obj.books.count()
            + obj.articles.count()
            + obj.audio.count()
            + obj.videos.count()
        )
    item_count.short_description = "Items"

    def bib_export_console_action(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one collection.", level=messages.ERROR)
            return
        return redirect(f"bib-export-console/{queryset.first().id}/")

    bib_export_console_action.short_description = "Export to BibTeX (.bib) in Vault"

    def bib_export_console_view(self, request, collection_id):
        collection = get_object_or_404(
            LibraryCollection.objects.prefetch_related(
                "books", "articles", "audio", "videos"
            ),
            id=collection_id,
        )
        form = LibraryBibExportForm(
            user=request.user,
            initial={"file_name": f"{collection.slug}.bib"},
        )

        if request.method == "POST":
            form = LibraryBibExportForm(request.POST, user=request.user)
            if form.is_valid():
                bib = collection_to_bibtex(collection)
                key = form.cleaned_data["file_name"]
                filename = f"{key}.bib"
                content = bib.encode("utf-8")

                vault_file = VaultFile(
                    owner=request.user,
                    title=filename,
                    key=key,
                    file_type="text",
                    bucket=form.cleaned_data["bucket"],
                    is_public=form.cleaned_data["make_public"],
                    notes=f"BibTeX export of library collection '{collection.title}'.",
                    content_hash=hashlib.sha256(content).hexdigest(),
                )
                vault_file.file.save(filename, ContentFile(content), save=False)
                vault_file.save()

                self.message_user(
                    request,
                    f"Exported '{collection.title}' to Vault as {filename}.",
                    level=messages.SUCCESS,
                )
                return redirect(
                    reverse("admin:library_librarycollection_change", args=[collection.id])
                )

        context = {
            **self.admin_site.each_context(request),
            "title": f"BibTeX Export: {collection.title}",
            "collection": collection,
            "form": form,
            "preview": collection_to_bibtex(collection),
            "back_url": reverse(
                "admin:library_librarycollection_change", args=[collection.id]
            ),
        }
        return render(request, "admin/library_bib_export_console.html", context)
