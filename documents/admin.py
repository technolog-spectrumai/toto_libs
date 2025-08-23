from django.contrib import admin
from .models import Tag, Office, Document, Section

# 🔧 Display toggles
SHOW_INLINE = True
SHOW_TABLE = True

# 🏷️ Tags
admin.site.register(Tag)

# 🏢 Office
@admin.register(Office)
class OfficeAdmin(admin.ModelAdmin):
    list_display = ['name', 'owner']
    search_fields = ['name']
    list_filter = ['owner']


# 📄 Document
if SHOW_INLINE:
    class SectionInline(admin.TabularInline):
        model = Section
        extra = 1
        ordering = ['order']
else:
    SectionInline = None

@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ['title', 'document_type', 'status', 'office', 'author', 'created_at']
    list_filter = ['document_type', 'status', 'office', 'tags']
    search_fields = ['title', 'summary', 'slug']
    readonly_fields = ['created_at']
    date_hierarchy = 'created_at'
    prepopulated_fields = {'slug': ('title',)}
    filter_horizontal = ['tags']
    inlines = [inline for inline in [SectionInline] if inline]


# 📎 Section
@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ['report', 'order', 'heading', 'created_at']
    list_filter = ['report', 'created_at']
    search_fields = ['heading', 'content']
