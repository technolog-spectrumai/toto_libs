from django import forms
from django.contrib import admin, messages
from nested_admin import NestedModelAdmin, NestedStackedInline
from django_tiptap.widgets import TipTapWidget
from django_ace import AceWidget

from documents.parsers import HTMLParser, LaTeXParser
from .models import (
    Tag, Department,
    Preset, LatexPreset, HTMLPreset,
    Document, Section, SubSection
)

# ────────────────────────────────────────────────
# 🔖 Tag & Department Admin
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
# ⚙️ Preset Admins
# ────────────────────────────────────────────────

@admin.register(LatexPreset)
class LatexPresetAdmin(admin.ModelAdmin):
    list_display = ['name', 'document_class']
    search_fields = ['name', 'document_class']

@admin.register(HTMLPreset)
class HTMLPresetAdmin(admin.ModelAdmin):
    list_display = ['name', 'template_name']
    search_fields = ['name', 'template_name']

# ────────────────────────────────────────────────
# 📘 SubSection Inline
# ────────────────────────────────────────────────

# ────────────────────────────────────────────────
# 📄 Document Form
# ────────────────────────────────────────────────

class DocumentForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = kwargs.get('instance')
        engine = getattr(instance.preset, 'engine', None) if instance and instance.preset else None

        if engine == 'html' and 'summary' in self.fields:
            self.fields['summary'].widget = TipTapWidget()

        if 'content' in self.fields:
            self.fields['content'].widget = (
                AceWidget(mode='latex', theme='chrome', width="100%", height="300px")
                if engine == 'latex' else TipTapWidget()
            )

# ────────────────────────────────────────────────
# 📚 Section Form
# ────────────────────────────────────────────────

class SectionForm(forms.ModelForm):
    class Meta:
        model = Section
        fields = ['title', 'order', 'content', 'document', 'deep']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        document = self.instance.document if self.instance.pk else self.initial.get('document')
        engine = getattr(document.preset, 'engine', None) if document and document.preset else None

        if self.instance.deep:
            self.fields['content'].widget = (
                AceWidget(mode='latex', theme='chrome', width="100%", height="300px")
                if engine == 'latex' else TipTapWidget()
            )
        else:
            self.fields['content'].widget = forms.HiddenInput()
            self.fields['content'].required = False

# ────────────────────────────────────────────────
# 📘 SubSection Form
# ────────────────────────────────────────────────

class SubSectionForm(forms.ModelForm):
    class Meta:
        model = SubSection
        fields = ['title', 'order', 'content', 'section']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        section = self.instance.section if self.instance.pk else self.initial.get('section')
        document = section.document if section else None
        engine = getattr(document.preset, 'engine', None) if document and document.preset else None

        if section and section.deep:
            self.fields['content'].widget = (
                AceWidget(mode='latex', theme='chrome')
                if engine == 'latex' else TipTapWidget()
            )
        else:
            self.fields['content'].widget = forms.HiddenInput()
            self.fields['content'].required = False

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
# 📄 Document Admin
# ────────────────────────────────────────────────

@admin.register(Document)
class DocumentAdmin(NestedModelAdmin):
    form = DocumentForm
    list_display = ['title', 'slug', 'version', 'created_by', 'created_at', 'engine', 'deep']
    list_filter = ['department', 'tags', 'created_at', 'deep']
    search_fields = ['title', 'summary', 'version', 'slug']
    filter_horizontal = ['tags']
    readonly_fields = ['created_at', 'engine_display']
    actions = ['expand']
    prepopulated_fields = {'slug': ('title',)}

    def get_inline_instances(self, request, obj=None):
        if not obj or not obj.deep:
            return []
        return [SectionInline(self.model, self.admin_site)]

    def engine_display(self, obj):
        return obj.engine or "—"
    engine_display.short_description = "Engine"

    def get_fields(self, request, obj=None):
        return [
            'title', 'slug', 'preset', 'summary', 'version',
            'created_by', 'tags', 'department', 'created_at', 'engine_display', 'deep', 'content'
        ]

    def expand(self, request, queryset):
        created_total = 0
        for document in queryset:
            if not document.deep:
                self.message_user(
                    request,
                    f"Document '{document.title}' is not marked as deep. Skipping.",
                    level=messages.WARNING
                )
                continue

            parser = HTMLParser() if document.engine == 'html' else LaTeXParser()
            sections = parser.build_sections(document=document, content=document.content or "")
            Section.objects.bulk_create(sections)
            created_total += len(sections)

        self.message_user(
            request,
            f"Expanded {created_total} sections from selected documents.",
            level=messages.SUCCESS
        )

    expand.short_description = "Expand content into sections"

# ────────────────────────────────────────────────
# 📚 Section Admin
# ────────────────────────────────────────────────

@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    form = SectionForm
    list_display = ['title', 'document', 'order', 'deep']
    list_filter = ['document', 'deep']
    search_fields = ['title', 'content']
    ordering = ['document', 'order']
    actions = ['expand']

    def expand(self, request, queryset):
        count = 0
        for section in queryset:
            if not section.deep:
                self.message_user(request, f"Section '{section.title}' is not marked as deep. Skipping.", level=messages.WARNING)
                continue

            document = section.document
            parser = HTMLParser() if document.engine == 'html' else LaTeXParser()
            subsections = parser.build_subsections(section, section.content or "")
            SubSection.objects.bulk_create(subsections)
            count += len(subsections)

        self.message_user(request, f"Expanded {count} subsections from selected sections.", level=messages.SUCCESS)

    expand.short_description = "Expand content into subsections"

# ────────────────────────────────────────────────
# 📘 SubSection Admin
# ────────────────────────────────────────────────

@admin.register(SubSection)
class SubSectionAdmin(admin.ModelAdmin):
    form = SubSectionForm
    list_display = ['title', 'section', 'order']
    list_filter = ['section__document']
    search_fields = ['title', 'content']
    ordering = ['section__document', 'section', 'order']
