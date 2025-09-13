from django import forms
from django.contrib import admin, messages
from nested_admin import NestedModelAdmin, NestedStackedInline
from django_tiptap.widgets import TipTapWidget
from django_ace import AceWidget

from documents.parsers import HTMLParser, LaTeXParser
from .models import (
    Tag, Department,
    LatexPreset, HTMLPreset,
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
    list_display = ['name', 'owner', 'copyright_holder']
    search_fields = ['name', 'copyright_holder']
    list_filter = ['owner']
    readonly_fields = ['owner']
    fieldsets = (
        (None, {'fields': ['name', 'owner', 'seal']}),
        ('Legal Metadata', {'fields': ['copyright_holder', 'copyright_notice']}),
    )

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
# 📄 Document Form
# ────────────────────────────────────────────────

class DocumentForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = kwargs.get('instance')

        if instance:
            if instance.html_preset:
                self.fields['summary'].widget = TipTapWidget()
            if instance.latex_preset:
                self.fields['summary'].widget = AceWidget(mode='latex', theme='chrome')

# ────────────────────────────────────────────────
# 📚 Section Form
# ────────────────────────────────────────────────

class SectionForm(forms.ModelForm):
    class Meta:
        model = Section
        fields = ['title', 'order', 'document', 'latex_content', 'html_content']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        doc = self.instance.document if self.instance.pk else self.initial.get('document')

        if doc and self.instance.deep:
            if doc.latex_preset:
                self.fields['latex_content'].widget = AceWidget(mode='latex', theme='chrome')
            else:
                self.fields['latex_content'].widget = forms.HiddenInput()

            if doc.html_preset:
                self.fields['html_content'].widget = TipTapWidget()
            else:
                self.fields['html_content'].widget = forms.HiddenInput()
        else:
            self.fields['latex_content'].widget = forms.HiddenInput()
            self.fields['html_content'].widget = forms.HiddenInput()

# ────────────────────────────────────────────────
# 📘 SubSection Form
# ────────────────────────────────────────────────

class SubSectionForm(forms.ModelForm):
    class Meta:
        model = SubSection
        fields = ['title', 'order', 'section', 'latex_content', 'html_content']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        section = self.instance.section if self.instance.pk else self.initial.get('section')
        doc = section.document if section else None

        if section and section.deep:
            if doc.latex_preset:
                self.fields['latex_content'].widget = AceWidget(mode='latex', theme='chrome')
            else:
                self.fields['latex_content'].widget = forms.HiddenInput()

            if doc.html_preset:
                self.fields['html_content'].widget = TipTapWidget()
            else:
                self.fields['html_content'].widget = forms.HiddenInput()
        else:
            self.fields['latex_content'].widget = forms.HiddenInput()
            self.fields['html_content'].widget = forms.HiddenInput()

# ────────────────────────────────────────────────
# 📘 SubSection Inline
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
# 📄 Document Admin
# ────────────────────────────────────────────────

@admin.register(Document)
class DocumentAdmin(NestedModelAdmin):
    form = DocumentForm
    list_display = ['title', 'slug', 'version', 'created_by', 'created_at', 'engine_display', 'deep']
    list_filter = ['department', 'tags', 'created_at']
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
        if obj.latex_preset and obj.html_preset:
            return "LaTeX + HTML"
        elif obj.latex_preset:
            return "LaTeX"
        elif obj.html_preset:
            return "HTML"
        return "—"
    engine_display.short_description = "Engine"

    def get_fields(self, request, obj=None):
        return [
            'title', 'slug', 'summary', 'version',
            'created_by', 'tags', 'department', 'created_at',
            'latex_preset', 'html_preset', 'engine_display', 'deep'
        ]

    def expand(self, request, queryset):
        created_total = 0
        for document in queryset:
            if document.deep:
                self.message_user(request, f"'{document.title}' is already deep. Skipping.", level=messages.WARNING)
                continue
            document.deep = True
            document.save()
            parser = HTMLParser() if document.html_preset else LaTeXParser()
            sections = parser.build_sections(document=document, content=document.summary or "")
            Section.objects.bulk_create(sections)
            created_total += len(sections)

        self.message_user(request, f"Expanded {created_total} sections.", level=messages.SUCCESS)

    expand.short_description = "Expand summary into sections"

# ────────────────────────────────────────────────
# 📚 Section Admin
# ────────────────────────────────────────────────

@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    form = SectionForm
    list_display = ['title', 'document', 'order', 'deep']
    list_filter = ['document']
    search_fields = ['title', 'latex_content', 'html_content']
    ordering = ['document', 'order']
    actions = ['expand']

    def expand(self, request, queryset):
        count = 0
        for section in queryset:
            if section.deep:
                self.message_user(request, f"'{section.title}' is already deep. Skipping.", level=messages.WARNING)
                continue
            section.deep = True
            section.save()
            doc = section.document
            parser = HTMLParser() if doc.html_preset else LaTeXParser()
            subsections = parser.build_subsections(section, section.html_content or section.latex_content or "")
            SubSection.objects.bulk_create(subsections)
            count += len(subsections)

        self.message_user(request, f"Expanded {count} subsections.", level=messages.SUCCESS)

    expand.short_description = "Expand section into subsections"

# ────────────────────────────────────────────────
# 📘 SubSection Admin
# ────────────────────────────────────────────────

@admin.register(SubSection)
class SubSectionAdmin(admin.ModelAdmin):
    form = SubSectionForm
    list_display = ['title', 'section', 'order']
    list_filter = ['section__document']
    search_fields = ['title', 'latex_content', 'html_content']
    ordering = ['section__document', 'section', 'order']
