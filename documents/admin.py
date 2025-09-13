from django.contrib import admin
from django import forms
from django_tiptap.widgets import TipTapWidget
from django_ace import AceWidget

from .models import (
    Tag, Department,
    Preset, LatexPreset, HTMLPreset,
    Document, Section
)

# ────────────────────────────────────────────────
# 🔖 Tag & Department
# ────────────────────────────────────────────────

@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    search_fields = ['name']
    list_display = ['name']

@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ['name', 'owner']
    search_fields = ['name']
    list_filter = ['owner']

# ────────────────────────────────────────────────
# ⚙️ Presets
# ────────────────────────────────────────────────

@admin.register(Preset)
class PresetAdmin(admin.ModelAdmin):
    list_display = ['name', 'engine', 'description']
    list_filter = ['engine']
    search_fields = ['name', 'description']

@admin.register(LatexPreset)
class LatexPresetAdmin(admin.ModelAdmin):
    list_display = ['name', 'document_class']
    search_fields = ['name', 'document_class']

@admin.register(HTMLPreset)
class HTMLPresetAdmin(admin.ModelAdmin):
    list_display = ['name', 'template_name']
    search_fields = ['name', 'template_name']

# ────────────────────────────────────────────────
# 🧠 Section Admin Form with Dynamic Widget
# ────────────────────────────────────────────────

class SectionAdminForm(forms.ModelForm):
    class Meta:
        model = Section
        fields = ['title', 'order', 'content', 'document']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        document = self.instance.document if self.instance.pk else self.initial.get('document')
        engine = getattr(document.preset, 'engine', None) if document and document.preset else None

        if engine == 'latex':
            self.fields['content'].widget = AceWidget(mode='latex', theme='twilight', width="100%", height="300px")
        else:
            self.fields['content'].widget = TipTapWidget()

# ────────────────────────────────────────────────
# 📚 Section Inline for Document
# ────────────────────────────────────────────────

class SectionInline(admin.StackedInline):
    model = Section
    form = SectionAdminForm
    extra = 1
    fields = ['title', 'order', 'content']
    ordering = ['order']

# ────────────────────────────────────────────────
# 📄 Document Admin
# ────────────────────────────────────────────────

@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ['title', 'version', 'created_by', 'created_at', 'engine', 'short_summary']
    list_filter = ['department', 'tags', 'created_at']
    search_fields = ['title', 'summary', 'version']
    inlines = [SectionInline]
    filter_horizontal = ['tags']

    def short_summary(self, obj):
        return (obj.summary[:75] + '...') if obj.summary else "-"
    short_summary.short_description = "Summary"


# ────────────────────────────────────────────────
# 📘 Section Admin (Direct Access)
# ────────────────────────────────────────────────

@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    form = SectionAdminForm
    list_display = ['title', 'document', 'order']
    list_filter = ['document']
    search_fields = ['title', 'content']
    ordering = ['document', 'order']
