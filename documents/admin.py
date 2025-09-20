from django import forms
from django.contrib import admin
from django_ace import AceWidget
from django_tiptap.widgets import TipTapWidget
from django.core.files import File
from django.utils.text import slugify
import os
from nested_admin import NestedModelAdmin, NestedStackedInline
from documents.collector import LaTeXCollector, HTMLCollector
from .convert import LatexDocumentConverter, HTMLDocumentConverter
from .batch import BatchAction

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
    PDFFile, DocumentEditor, EditorSection, EditorSubSection,
    HtmlDocumentEditor, LatexDocumentEditor
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
class DocumentAdmin(PolymorphicParentModelAdmin):
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
    actions = ['compile_pdf', "convert_to_html"]

    @admin.action(description="🔁 Convert LaTeX to HTML (create new HTMLDocument)")
    def convert_to_html(self, request, queryset):
        def convert_one(document):
            html = LatexDocumentConverter(document).to_html()
            new_doc = HTMLDocument.objects.create(
                title=f"{document.title} (HTML)",
                content=html,
                created_by=document.created_by,
                department=document.department
            )
            new_doc.tags.set(document.tags.all())
            return new_doc

        result = BatchAction(queryset).run(convert_one)
        BatchAction.display_messages(result, self.message_user, request, verb="convert")

    @admin.action(description="Compile selected LaTeX documents to PDF")
    def compile_pdf(self, request, queryset):
        def compile_one(document):
            compiler = LatexCompiler(document)
            pdf_path = compiler.generate_pdf()

            if not os.path.exists(pdf_path):
                raise FileNotFoundError("PDF file not found after generation.")

            PDFFile.objects.filter(document=document).delete()

            with open(pdf_path, 'rb') as f:
                PDFFile.objects.create(
                    document=document,
                    file=File(f, name=f"{slugify(document.title)}.pdf")
                )

            return document

        result = BatchAction(queryset).run(compile_one)
        BatchAction.display_messages(result, self.message_user, request, verb="compile")

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
    actions = ["convert_to_latex"]

    @admin.action(description="🔁 Convert HTML to LaTeX (create new LatexDocument)")
    def convert_to_latex(self, request, queryset):
        def convert_one(document):
            latex = HTMLDocumentConverter(document).to_latex()
            new_doc = LatexDocument.objects.create(
                title=f"{document.title} (LaTeX)",
                content=latex,
                created_by=document.created_by,
                department=document.department
            )
            new_doc.tags.set(document.tags.all())
            return new_doc

        result = BatchAction(queryset).run(convert_one)
        BatchAction.display_messages(result, self.message_user, request, verb="convert")

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


def merge_editor_into_document(real_editor, collector=None):
    if not real_editor.document:
        raise RuntimeError(f"Editor {real_editor} has no linked document to merge into.")
    if not collector:
        raise ValueError("No collector instance provided. Collector must be explicitly passed.")
    collector.collect()
    document = real_editor.document.get_real_instance()
    document.content = collector.render()
    document.save()
    return real_editor


@admin.register(DocumentEditor)
class DocumentEditorAdmin(PolymorphicParentModelAdmin, NestedModelAdmin):
    base_model = DocumentEditor
    child_models = (HtmlDocumentEditor, LatexDocumentEditor)
    list_display = ['title', 'slug', 'version', 'created_by', 'created_at']
    list_filter = ['department', 'tags', 'created_at']
    search_fields = ['title', 'summary', 'slug']
    filter_horizontal = ['tags']
    readonly_fields = ['created_at']
    prepopulated_fields = {'slug': ('title',)}
    inlines = [EditorSectionInline]
    actions = ['merge_into_document']

    @admin.action(description="🧩 Merge editor content into linked document")
    def merge_into_document(self, request, queryset):
        def merge_one(editor):
            real_editor = editor.get_real_instance()
            if not real_editor.document:
                raise RuntimeError("Editor {editor} has no linked document to merge into.")
            document = real_editor.document.get_real_instance()
            collector = None
            if isinstance(document, LatexDocument):
                collector = LaTeXCollector(real_editor)
            elif isinstance(document, HTMLDocument):
                collector = HTMLCollector(real_editor)
            merge_editor_into_document(real_editor, collector)
            return editor
        result = BatchAction(queryset).run(merge_one)
        BatchAction.display_messages(result, self.message_user, request, verb="merge")


@admin.register(HtmlDocumentEditor)
class HtmlDocumentEditorAdmin(PolymorphicChildModelAdmin, VersionAdmin, NestedModelAdmin):
    base_model = HtmlDocumentEditor
    list_display = ['title', 'slug', 'version', 'created_by', 'created_at']
    readonly_fields = ['created_at']
    inlines = [EditorSectionInline]


@admin.register(LatexDocumentEditor)
class LatexDocumentEditorAdmin(PolymorphicChildModelAdmin, VersionAdmin, NestedModelAdmin):
    base_model = LatexDocumentEditor
    list_display = ['title', 'slug', 'version', 'created_by', 'created_at', 'preset']
    readonly_fields = ['created_at']
    inlines = [EditorSectionInline]


