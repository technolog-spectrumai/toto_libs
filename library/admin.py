from django.contrib import admin
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
)

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
    """Parent admin that shows all reference items together."""
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
    list_display = ("title", "order", "polymorphic_ctype", "tag_list")
    search_fields = ("title",)

    def tag_list(self, obj):
        return ", ".join(tag.name for tag in obj.tags.all())
    tag_list.short_description = "Tags"


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


# 🌐 Website Reference Admin
@admin.register(WebsiteReference)
class WebsiteReferenceAdmin(PolymorphicChildModelAdmin):
    base_model = WebsiteReference
    list_display = ("title", "site_name", "url", "author", "year", "accessed_date", "tag_list")
    search_fields = ("title", "site_name", "author", "url")

    def tag_list(self, obj):
        return ", ".join(tag.name for tag in obj.tags.all())


# 🗂 Generic Reference Admin
@admin.register(GenericReference)
class GenericReferenceAdmin(PolymorphicChildModelAdmin):
    base_model = GenericReference
    list_display = ("title", "author", "source_type", "url", "year", "tag_list")
    search_fields = ("title", "author", "source_type", "url")

    def tag_list(self, obj):
        return ", ".join(tag.name for tag in obj.tags.all())
