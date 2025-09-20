from django import forms
from django.contrib import admin
from django_ace import AceWidget
from django_tiptap.widgets import TipTapWidget
from django.core.files import File
from django.utils.text import slugify
import os
from nested_admin import NestedModelAdmin, NestedStackedInline
from documents.collector import gather_editor_content


from reversion.admin import VersionAdmin
from polymorphic.admin import (
    PolymorphicParentModelAdmin,
    PolymorphicChildModelAdmin,
    PolymorphicChildModelFilter
)

from .pdf import LatexCompiler
from .models import (
    Tag, Department,
    LatexPreset, HTMLPreset,
    Document, LatexDocument, HTMLDocument,
    PDFFile, DocumentEditor, EditorSection, EditorSubSection
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
    list_display = ['name', 'owner', 'copyright_holder']
    search_fields = ['name', 'copyright_holder']
    list_filter = ['owner']
    readonly_fields = ['owner']
    fieldsets = (
        (None, {'fields': ['name', 'owner', 'seal']}),
        ('Legal Metadata', {'fields': ['copyright_holder', 'copyright_notice']}),
    )

# ────────────────────────────────────────────────
# 🎨 Presets
# ────────────────────────────────────────────────

@admin.register(LatexPreset)
class LatexPresetAdmin(admin.ModelAdmin):
    list_display = ['name', 'document_class']
    search_fields = ['name', 'document_class']


@admin.register(HTMLPreset)
class HTMLPresetAdmin(admin.ModelAdmin):
    list_display = ['name', 'has_style_mapping']
    search_fields = ['name']
    fieldsets = (
        (None, {'fields': ['name', 'description']}),
        ('Styling Configuration', {'fields': ['css_classes', 'style_mapping']}),
        ('Footer', {'fields': ['footer_html']}),
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
# 📄 Polymorphic Document Admin
# ────────────────────────────────────────────────

@admin.register(Document)
class DocumentAdmin(PolymorphicParentModelAdmin, VersionAdmin):
    base_model = Document
    child_models = (LatexDocument, HTMLDocument)
    list_display = ['title', 'created_by', 'created_at', 'get_type']
    list_filter = [PolymorphicChildModelFilter, 'department', 'tags']
    search_fields = ['title', 'slug']
    readonly_fields = ['created_at']
    filter_horizontal = ['tags']
    prepopulated_fields = {'slug': ('title',)}

    @admin.display(description="Type")
    def get_type(self, obj):
        return obj.document_type

# ────────────────────────────────────────────────
# 📄 LaTeX Document Admin
# ────────────────────────────────────────────────

class LatexDocumentForm(forms.ModelForm, LatexWidgetMixin):
    class Meta:
        model = LatexDocument
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.configure_widgets()


@admin.register(LatexDocument)
class LatexDocumentAdmin(PolymorphicChildModelAdmin, VersionAdmin):
    base_model = LatexDocument
    form = LatexDocumentForm
    list_display = ['title', 'slug', 'version', 'created_by', 'created_at']
    readonly_fields = ['created_at']
    actions = ['compile_pdf']

    @admin.action(description="Compile selected LaTeX documents to PDF")
    def compile_pdf(self, request, queryset):
        compiled = 0
        failed = 0

        for document in queryset:
            try:
                converter = LatexCompiler(document)
                pdf_path = converter.generate_pdf()

                if not os.path.exists(pdf_path):
                    raise FileNotFoundError("PDF file not found after generation.")

                PDFFile.objects.filter(document=document).delete()

                with open(pdf_path, 'rb') as f:
                    PDFFile.objects.create(
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

# ────────────────────────────────────────────────
# 🌐 HTML Document Admin
# ────────────────────────────────────────────────

class HTMLDocumentForm(forms.ModelForm, HTMLWidgetMixin):
    class Meta:
        model = HTMLDocument
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.configure_widgets()


@admin.register(HTMLDocument)
class HTMLDocumentAdmin(PolymorphicChildModelAdmin, VersionAdmin):
    base_model = HTMLDocument
    form = HTMLDocumentForm
    list_display = ['title', 'slug', 'version', 'created_by', 'created_at']
    readonly_fields = ['created_at']

# ────────────────────────────────────────────────
# 📎 PDF File Admin
# ────────────────────────────────────────────────

@admin.register(PDFFile)
class PDFFileAdmin(admin.ModelAdmin):
    list_display = ['document', 'created_at']
    readonly_fields = ['created_at']
    search_fields = ['document__title']
    list_filter = ['created_at']
    ordering = ['-created_at']


class FormatAwareWidgetMixin:
    def apply_format_widgets(self, fields, document_instance):
        if isinstance(document_instance, HTMLDocument):
            for field in fields:
                self.fields[field].widget = TipTapWidget()
        else:
            for field in fields:
                self.fields[field].widget = AceWidget(mode='latex', theme='chrome')


class EditorSectionForm(forms.ModelForm, FormatAwareWidgetMixin):
    class Meta:
        model = EditorSection
        fields = ['title', 'order', 'document', 'content']

    def __init__(self, *args, document_instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_format_widgets(['content'], document_instance)


class EditorSubSectionForm(forms.ModelForm, FormatAwareWidgetMixin):
    class Meta:
        model = EditorSubSection
        fields = ['title', 'order', 'section', 'content']

    def __init__(self, *args, document_instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_format_widgets(['content'], document_instance)


class FormatAwareInline(NestedStackedInline):
    form_class = None  # override in subclass

    def get_document(self, obj):
        return None

    def get_formset(self, request, obj=None, **kwargs):
        document_instance = self.get_document(obj)

        class InlineForm(self.form_class):
            def __init__(self2, *args, **form_kwargs):
                super().__init__(*args, document_instance=document_instance, **form_kwargs)

        kwargs['form'] = InlineForm
        return super().get_formset(request, obj, **kwargs)


class EditorSubSectionInline(FormatAwareInline):
    model = EditorSubSection
    form_class = EditorSubSectionForm
    extra = 1
    ordering = ['order']

    def get_document(self, obj):
        if obj and hasattr(obj, 'document'):
            return obj.document.document.get_real_instance()
        return None


class EditorSectionInline(FormatAwareInline):
    model = EditorSection
    form_class = EditorSectionForm
    extra = 1
    ordering = ['order']
    inlines = [EditorSubSectionInline]

    def get_document(self, obj):
        if obj and hasattr(obj, 'document'):
            return obj.document.get_real_instance()
        return None


@admin.register(DocumentEditor)
class DocumentEditorAdmin(NestedModelAdmin, VersionAdmin):
    #form = DocumentEditorForm
    list_display = ['title', 'slug', 'version', 'created_by', 'created_at', 'document']
    list_filter = ['department', 'tags', 'created_at']
    search_fields = ['title', 'summary', 'slug']
    filter_horizontal = ['tags']
    readonly_fields = ['created_at']
    prepopulated_fields = {'slug': ('title',)}
    inlines = [EditorSectionInline]
    actions = ['merge_into_document']

    @admin.action(description="🧩 Merge editor content into linked document")
    def merge_into_document(self, request, queryset):
        merged = 0
        failed = 0

        for editor in queryset:
            try:
                content = gather_editor_content(editor)
                doc = editor.document.get_real_instance()
                doc.content = content
                doc.save()
                merged += 1
            except Exception as e:
                failed += 1
                self.message_user(request, f"❌ Failed to merge '{editor.title}': {e}", level='error')

        self.message_user(
            request,
            f"✅ Merged {merged} editor(s) into document. {'⚠️ ' + str(failed) + ' failed.' if failed else ''}",
            level='info'
        )

