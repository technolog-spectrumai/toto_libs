import datetime
import json

from django.contrib import admin
from django.http import HttpResponse
from django.template.defaultfilters import slugify
from django.utils.html import format_html
from polymorphic.admin import (
    PolymorphicParentModelAdmin,
    PolymorphicChildModelAdmin,
    PolymorphicChildModelFilter,
)
from .models import (
    ReferenceItem,
    ReferenceTag,
    BookReference,
    JournalReference,
    VideoReference,
    AudioReference,
    WebsiteReference,
    GenericReference,
    Library
)
from toto.batch import BatchAction


@admin.register(Library)
class LibraryAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "reference_count", "created_at")
    search_fields = ("name", "owner__username", "owner__email")
    list_filter = ("owner",)
    filter_horizontal = ("references",)
    actions = ["export_json"]

    def reference_count(self, obj):
        return obj.references.count()
    reference_count.short_description = "References"

    @admin.action(description="Download JSON file for selected libraries")
    def export_json(self, request, queryset):

        # Single library → simple JSON export
        if queryset.count() == 1:
            library = queryset.first()
            filename = f"{slugify(library.name)}.json"
            content = json.dumps(library.to_json(), indent=2)

        else:
            # Multiple libraries → combined JSON array
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
            filename = f"libraries_{timestamp}.json"

            content = json.dumps(
                [lib.to_json() for lib in queryset],
                indent=2
            )

        # Create downloadable response
        response = HttpResponse(content, content_type="application/json")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


# ────────────────────────────────────────────────
# 🏷️ ReferenceTag Admin
# ────────────────────────────────────────────────

@admin.register(ReferenceTag)
class ReferenceTagAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


# ────────────────────────────────────────────────
# 🔖 Base ReferenceItem Admin (Parent)
# ────────────────────────────────────────────────

@admin.register(ReferenceItem)
class ReferenceItemParentAdmin(PolymorphicParentModelAdmin):
    base_model = ReferenceItem
    child_models = (
        BookReference,
        JournalReference,
        VideoReference,
        AudioReference,
        WebsiteReference,
        GenericReference,
    )
    list_filter = (PolymorphicChildModelFilter,)
    list_display = (
        "title",
        "order",
        "polymorphic_ctype",
        "tag_list",
        "library_list",
        "download_link",
    )
    search_fields = ("title",)

    def tag_list(self, obj):
        return ", ".join(tag.name for tag in obj.tags.all())
    tag_list.short_description = "Tags"

    def library_list(self, obj):
        return ", ".join(lib.name for lib in obj.libraries.all())
    library_list.short_description = "Libraries"

    def download_link(self, obj):
        if obj.vault_file and obj.vault_file.file:
            return format_html(
                '<a href="{}" download>Download</a>', obj.vault_file.file.url
            )
        return "-"
    download_link.short_description = "Download"


# ────────────────────────────────────────────────
# 📖 Book Reference Admin
# ────────────────────────────────────────────────

@admin.register(BookReference)
class BookReferenceAdmin(PolymorphicChildModelAdmin):
    base_model = BookReference
    list_display = ("title", "author", "publisher", "year", "isbn", "tag_list")
    search_fields = ("title", "author", "isbn")

    def tag_list(self, obj):
        return ", ".join(tag.name for tag in obj.tags.all())


# 📰 Journal Reference Admin
@admin.register(JournalReference)
class JournalReferenceAdmin(PolymorphicChildModelAdmin):
    base_model = JournalReference
    list_display = ("title", "author", "journal", "year", "doi", "tag_list")
    search_fields = ("title", "author", "journal", "doi")

    def tag_list(self, obj):
        return ", ".join(tag.name for tag in obj.tags.all())


# 🎥 Video Reference Admin
@admin.register(VideoReference)
class VideoReferenceAdmin(PolymorphicChildModelAdmin):
    base_model = VideoReference
    list_display = ("title", "creator", "platform", "url", "year", "tag_list")
    search_fields = ("title", "creator", "platform")

    def tag_list(self, obj):
        return ", ".join(tag.name for tag in obj.tags.all())


# 🎵 Audio Reference Admin
@admin.register(AudioReference)
class AudioReferenceAdmin(PolymorphicChildModelAdmin):
    base_model = AudioReference
    list_display = ("title", "artist", "album", "url", "year", "tag_list")
    search_fields = ("title", "artist", "album")

    def tag_list(self, obj):
        return ", ".join(tag.name for tag in obj.tags.all())


@admin.register(WebsiteReference)
class WebsiteReferenceAdmin(PolymorphicChildModelAdmin):
    base_model = WebsiteReference
    list_display = ("title", "sitename", "url", "author", "year", "accessed_date", "tag_list")
    search_fields = ("title", "sitename", "author", "url")

    def tag_list(self, obj):
        return ", ".join(tag.name for tag in obj.tags.all())


# 🗂 Generic Reference Admin
@admin.register(GenericReference)
class GenericReferenceAdmin(PolymorphicChildModelAdmin):
    base_model = GenericReference
    list_display = ("title", "author", "sourcetype", "url", "year", "tag_list")
    search_fields = ("title", "author", "sourcetype", "url")

    def tag_list(self, obj):
        return ", ".join(tag.name for tag in obj.tags.all())
