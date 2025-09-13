from django import forms
from django.contrib import admin
from nested_admin import NestedModelAdmin, NestedStackedInline
from django_tiptap.widgets import TipTapWidget
from django_ace import AceWidget

from .models import (
    Tag, Department,
    Preset, LatexPreset, HTMLPreset,
    Document, Section, SubSection
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
# 🧠 Section Form with Dynamic Widget
# ────────────────────────────────────────────────

class SectionForm(forms.ModelForm):
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
# 📘 SubSection Form with Dynamic Widget
# ────────────────────────────────────────────────

class SubSectionForm(forms.ModelForm):
    class Meta:
        model = SubSection
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = kwargs.get('instance')
        theme = 'chrome'
        if instance and hasattr(instance.section.document, 'engine'):
            engine = instance.section.document.engine
            if engine == 'latex':
                if hasattr(instance, 'use_light_mode') and instance.use_light_mode is False:
                    theme = 'monokai'
                self.fields['content'].widget = AceWidget(mode='latex', theme=theme)
            elif engine == 'html':
                self.fields['content'].widget = TipTapWidget()

# ────────────────────────────────────────────────
# 📚 SubSection Inline
# ────────────────────────────────────────────────

class SubSectionInline(NestedStackedInline):
    model = SubSection
    form = SubSectionForm
    extra = 1
    ordering = ['order']

# ────────────────────────────────────────────────
# 📚 Section Inline
# ────────────────────────────────────────────────

class SectionInline(NestedStackedInline):
    model = Section
    form = SectionForm
    extra = 1
    ordering = ['order']
    inlines = [SubSectionInline]

# ────────────────────────────────────────────────
# 📄 Document Admin with Nested Sections
# ────────────────────────────────────────────────

@admin.register(Document)
class DocumentAdmin(NestedModelAdmin):
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
    form = SectionForm
    list_display = ['title', 'document', 'order']
    list_filter = ['document']
    search_fields = ['title', 'content']
    ordering = ['document', 'order']
