from django import forms
from django.contrib import admin
from nested_admin import NestedModelAdmin, NestedStackedInline
from django_tiptap.widgets import TipTapWidget
from django_ace import AceWidget
from documents.parsers import HTMLParser, LaTeXParser
from django.contrib import messages


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
# ⚙️ Depth Label Utility
# ────────────────────────────────────────────────

def get_depth_label(depth: int) -> str:
    return {
        1: "Document-level content",
        2: "Section-level content",
        3: "SubSection-level content"
    }.get(depth, f"Unknown ({depth})")

# ────────────────────────────────────────────────
# ⚙️ Presets
# ────────────────────────────────────────────────


@admin.register(LatexPreset)
class LatexPresetAdmin(admin.ModelAdmin):
    list_display = ['name', 'document_class', 'depth_label']
    search_fields = ['name', 'document_class']

    def depth_label(self, obj):
        return get_depth_label(obj.depth)
    depth_label.short_description = "Content Depth"


@admin.register(HTMLPreset)
class HTMLPresetAdmin(admin.ModelAdmin):
    list_display = ['name', 'template_name', 'depth_label']
    search_fields = ['name', 'template_name']

    def depth_label(self, obj):
        return get_depth_label(obj.depth)
    depth_label.short_description = "Content Depth"


def configure_content_widget(fields, engine, depth, expected_depth, width="100%", height="300px"):
    """
    Dynamically configures or removes the 'content' field based on depth and engine.
    """
    if 'content' not in fields:
        return

    if depth == expected_depth:
        fields['content'].widget = (
            AceWidget(mode='latex', theme='chrome', width=width, height=height)
            if engine == 'latex' else TipTapWidget()
        )
    else:
        fields.pop('content', None)


# ────────────────────────────────────────────────
# 🧠 Section Form
# ────────────────────────────────────────────────

class SectionForm(forms.ModelForm):
    class Meta:
        model = Section
        fields = ['title', 'order', 'content', 'document']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        document = self.instance.document if self.instance.pk else self.initial.get('document')
        preset = getattr(document, 'preset', None) if document else None
        engine = getattr(preset, 'engine', None) if preset else None
        depth = getattr(preset, 'depth', None) if preset else None

        if depth == 2:
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

        instance = kwargs.get('instance')
        document = instance.section.document if instance and instance.section else None
        preset = getattr(document, 'preset', None) if document else None
        engine = getattr(preset, 'engine', None) if preset else None
        depth = getattr(preset, 'depth', None) if preset else None

        if depth == 3:
            self.fields['content'].widget = (
                AceWidget(mode='latex', theme='chrome')
                if engine == 'latex' else TipTapWidget()
            )
        else:
            self.fields['content'].widget = forms.HiddenInput()
            self.fields['content'].required = False



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
# 📄 Document Form
# ───────────────────────────────────────────────

class DocumentForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        instance = kwargs.get('instance')
        preset = getattr(instance, 'preset', None)

        if preset:
            engine = preset.engine
            depth = preset.depth

            if engine == 'html' and 'summary' in self.fields:
                self.fields['summary'].widget = TipTapWidget()

            if depth == 1 and 'content' in self.fields:
                self.fields['content'].widget = (
                    AceWidget(mode='latex', theme='chrome', width="100%", height="300px")
                    if engine == 'latex' else TipTapWidget()
                )
            elif 'content' in self.fields:
                self.fields.pop('content')


# ────────────────────────────────────────────────
# 📄 Document Admin
# ────────────────────────────────────────────────

@admin.register(Document)
class DocumentAdmin(NestedModelAdmin):
    form = DocumentForm
    list_display = ['title', 'slug', 'version', 'created_by', 'created_at', 'engine', 'depth']
    list_filter = ['department', 'tags', 'created_at']
    search_fields = ['title', 'summary', 'version', 'slug']
    filter_horizontal = ['tags']
    readonly_fields = ['created_at']
    actions = ['expand']

    prepopulated_fields = {'slug': ('title',)}

    def get_inline_instances(self, request, obj=None):
        if not obj or not obj.preset:
            return []

        depth = obj.preset.depth

        if depth == 1:
            return []

        elif depth == 2:
            class FlatSectionInline(NestedStackedInline):
                model = Section
                form = SectionForm
                extra = 1
                ordering = ['order']
                inlines = []
            return [FlatSectionInline(self.model, self.admin_site)]

        elif depth == 3:
            return [SectionInline(self.model, self.admin_site)]

        return []

    def get_fields(self, request, obj=None):
        base_fields = [
            'title', 'slug', 'preset', 'summary', 'version',
            'created_by', 'tags', 'department', 'created_at'
        ]
        if obj and obj.preset and obj.preset.depth == 1:
            base_fields.append('content')
        return base_fields

    def expand(self, request, queryset):
        created_total = 0

        for document in queryset:
            if document.depth != 1:
                self.message_user(
                    request,
                    f"Document '{document.title}' is depth {document.depth}. Only depth 1 documents can be expanded.",
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
# 📘 Section Admin
# ────────────────────────────────────────────────

@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    form = SectionForm
    list_display = ['title', 'document', 'order']
    list_filter = ['document']
    search_fields = ['title', 'content']
    ordering = ['document', 'order']
    actions = ['expand']

    def expand(self, request, queryset):
        count = 0
        for section in queryset:
            document = section.document
            engine = document.engine

            # Choose parser
            if engine == 'html':
                parser = HTMLParser()
            elif engine == 'latex':
                parser = LaTeXParser()
            else:
                self.message_user(request, f"Unknown engine for document '{document.title}'", level=messages.WARNING)
                continue

            # Build subsections from section content
            subsections = parser.build_subsections(section, section.content or "")
            SubSection.objects.bulk_create(subsections)
            count += len(subsections)

        self.message_user(request, f"Expanded {count} subsections from selected sections.", level=messages.SUCCESS)

    expand.short_description = "Expand content into subsections"


@admin.register(SubSection)
class SubSectionAdmin(admin.ModelAdmin):
    form = SubSectionForm
    list_display = ['title', 'section', 'order']
    list_filter = ['section__document']
    search_fields = ['title', 'content']
    ordering = ['section__document', 'section', 'order']

