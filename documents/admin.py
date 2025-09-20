from django import forms
from django.contrib import admin
from nested_admin import NestedModelAdmin, NestedStackedInline
from django_tiptap.widgets import TipTapWidget
from django_ace import AceWidget
from documents.parsers import LaTeXParser
from .pdf import LaTeXToPDFConverter
from django.core.files import File
from django.utils.text import slugify
import os
from .models import HTMLPreview
from .convert import LaTeXToHTMLConverter
from .models import (
    Tag, Department,
    LatexPreset, HTMLPreset,
    LatexDocument, LatexSection, LatexSubSection, PDFFile
)
from reversion.admin import VersionAdmin


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


@admin.register(LatexPreset)
class LatexPresetAdmin(admin.ModelAdmin):
    list_display = ['name', 'document_class']
    search_fields = ['name', 'document_class']


@admin.register(HTMLPreset)
class HTMLPresetAdmin(admin.ModelAdmin):
    list_display = ['name', 'has_style_mapping']
    search_fields = ['name']
    readonly_fields = []

    fieldsets = (
        (None, {
            'fields': ['name', 'description']
        }),
        ('Styling Configuration', {
            'fields': ['css_classes', 'style_mapping']
        }),
        ('Footer', {
            'fields': ['footer_html']
        }),
    )

    @admin.display(description="Has Style Mapping")
    def has_style_mapping(self, obj):
        return bool(obj.style_mapping)

# ────────────────────────────────────────────────
# 🧩 Widget Mixins
# ────────────────────────────────────────────────

class LatexWidgetMixin:
    def configure_widgets(self):
        self.fields['content'].widget = AceWidget(mode='latex', theme='chrome')


class HTMLWidgetMixin:
    def configure_widgets(self):
        self.fields['content'].widget = TipTapWidget()

# ────────────────────────────────────────────────
# 📄 LaTeX Admin
# ────────────────────────────────────────────────

class LatexDocumentForm(forms.ModelForm):
    class Meta:
        model = LatexDocument
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['summary'].widget = AceWidget(mode='latex', theme='chrome')


class LatexSubSectionForm(forms.ModelForm, LatexWidgetMixin):
    class Meta:
        model = LatexSubSection
        fields = ['title', 'order', 'section', 'content']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.configure_widgets()


class LatexSectionForm(forms.ModelForm, LatexWidgetMixin):
    class Meta:
        model = LatexSection
        fields = ['title', 'order', 'document', 'content']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.configure_widgets()


class LatexSubSectionInline(NestedStackedInline):
    model = LatexSubSection
    form = LatexSubSectionForm
    extra = 1
    ordering = ['order']


class LatexSectionInline(NestedStackedInline):
    model = LatexSection
    form = LatexSectionForm
    extra = 1
    ordering = ['order']
    inlines = [LatexSubSectionInline]


@admin.register(LatexDocument)
class LatexDocumentAdmin(NestedModelAdmin, VersionAdmin):
    form = LatexDocumentForm
    list_display = ['title', 'slug', 'version', 'created_by', 'created_at', 'deep', 'flat']
    list_filter = ['department', 'tags', 'created_at']
    search_fields = ['title', 'summary', 'version', 'slug']
    filter_horizontal = ['tags']
    readonly_fields = ['created_at']
    prepopulated_fields = {'slug': ('title',)}
    actions = ['compile_pdf', 'generate_html_preview']

    def get_inline_instances(self, request, obj=None):
        if obj and obj.deep and not obj.flat:
            return [LatexSectionInline(self.model, self.admin_site)]
        return []

    @admin.action(description="Compile selected LaTeX documents to PDF")
    def compile_pdf(self, request, queryset):
        compiled = 0
        failed = 0

        for document in queryset:
            try:
                converter = LaTeXToPDFConverter(document)
                pdf_path = converter.generate_pdf()

                if not os.path.exists(pdf_path):
                    raise FileNotFoundError("PDF file not found after generation.")

                # Remove existing PDFFile if it exists
                PDFFile.objects.filter(document=document).delete()

                # Save PDF to media directory
                with open(pdf_path, 'rb') as f:
                    pdf_file = PDFFile.objects.create(
                        document=document,
                        file=File(f, name=f"{slugify(document.title)}.pdf")
                    )

                compiled += 1

            except Exception as e:
                failed += 1
                self.message_user(request, f"❌ Failed to compile '{document.title}': {e}", level='error')

        self.message_user(
            request,
            f"✅ Compiled {compiled} document(s) to PDF. {'⚠️ ' + str(failed) + ' failed.' if failed else ''}",
            level='info'
        )

    @admin.action(description="Generate HTML preview from LaTeX content")
    def generate_html_preview(self, request, queryset):
        generated = 0
        failed = 0

        for document in queryset:
            try:
                converter = LaTeXToHTMLConverter()
                html_content = converter.convert_document(document)

                HTMLPreview.objects.update_or_create(
                    latex_document=document,
                    defaults={'content': html_content}
                )
                generated += 1

            except Exception as e:
                failed += 1
                self.message_user(request, f"❌ Failed to generate preview for '{document.title}': {e}", level='error')

        self.message_user(
            request,
            f"✅ Generated previews for {generated} document(s). {'⚠️ ' + str(failed) + ' failed.' if failed else ''}",
            level='info'
        )

@admin.register(LatexSection)
class LatexSectionAdmin(VersionAdmin):
    form = LatexSectionForm
    list_display = ['title', 'document', 'order', 'deep']
    list_filter = ['document']
    search_fields = ['title', 'content']
    ordering = ['document', 'order']


@admin.register(LatexSubSection)
class LatexSubSectionAdmin(VersionAdmin):
    form = LatexSubSectionForm
    list_display = ['title', 'section', 'order']
    list_filter = ['section__document']
    search_fields = ['title', 'content']
    ordering = ['section__document', 'section', 'order']


@admin.register(PDFFile)
class PDFFileAdmin(admin.ModelAdmin):
    list_display = ['document', 'created_at']
    readonly_fields = ['created_at']
    search_fields = ['document__title']
    list_filter = ['created_at']
    ordering = ['-created_at']


class HTMLPreviewForm(forms.ModelForm, HTMLWidgetMixin):
    class Meta:
        model = HTMLPreview
        fields = ['latex_document', 'content', 'preset']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.configure_widgets()


@admin.register(HTMLPreview)
class HTMLPreviewAdmin(admin.ModelAdmin):
    form = HTMLPreviewForm
    list_display = ['latex_document', 'get_author', 'get_preset']
    search_fields = ['latex_document__title', 'latex_document__slug']
    list_filter = ['latex_document__created_at', 'preset']
    readonly_fields = ['latex_document']

    @admin.display(description="Author")
    def get_author(self, obj):
        return obj.latex_document.created_by.get_full_name() if obj.latex_document.created_by else "—"

    @admin.display(description="Preset")
    def get_preset(self, obj):
        return obj.preset.name if obj.preset else "—"






