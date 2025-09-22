from django.contrib import admin
from django import forms
from django.utils.text import slugify
from django.core.files import File
from nested_admin import NestedModelAdmin, NestedStackedInline
from reversion.admin import VersionAdmin
from .pdf import LatexCompiler
from .models import (
    Tag, Department,
    LatexPreset, HTMLPreset,
    Document, DocumentSection, DocumentSubSection,
    PDFFile, HTMLFile
)
from .convert import LatexDocumentConverter
from .batch import BatchAction
from django.core.files.base import ContentFile
from django_ace import AceWidget
from django_tiptap.widgets import TipTapWidget
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
    readonly_fields = ['owner']
    fieldsets = (
        (None, {'fields': ['name', 'owner', 'seal']}),
    )

# ────────────────────────────────────────────────
# 🎨 Presets Admin
# ────────────────────────────────────────────────

@admin.register(LatexPreset)
class LatexPresetAdmin(admin.ModelAdmin):
    list_display = ['name', 'document_class']
    search_fields = ['name', 'document_class']


@admin.register(HTMLPreset)
class HTMLPresetAdmin(admin.ModelAdmin):
    list_display = ['name', 'has_style_mapping']
    search_fields = ['name']

    @admin.display(description="Has Style Mapping")
    def has_style_mapping(self, obj):
        return bool(obj.style_mapping)

# ────────────────────────────────────────────────
# 🧩 Document Admin
# ────────────────────────────────────────────────

class DocumentForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['content'].widget = AceWidget(mode='latex', theme='chrome')
        self.fields['content'].widget.attrs.update({'style': 'font-family: monospace;'})

class DocumentSectionForm(forms.ModelForm):
    class Meta:
        model = DocumentSection
        fields = ['title', 'order', 'content']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['content'].widget = AceWidget(mode='latex', theme='chrome')
        self.fields['content'].widget.attrs.update({'style': 'font-family: monospace;'})


class DocumentSubSectionForm(forms.ModelForm):
    class Meta:
        model = DocumentSubSection
        fields = ['title', 'order', 'content']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['content'].widget = AceWidget(mode='latex', theme='chrome')
        self.fields['content'].widget.attrs.update({'style': 'font-family: monospace;'})

class DocumentSubSectionInline(NestedStackedInline):
    model = DocumentSubSection
    form = DocumentSubSectionForm
    extra = 1
    ordering = ['order']

class DocumentSectionInline(NestedStackedInline):
    model = DocumentSection
    form = DocumentSectionForm
    extra = 1
    ordering = ['order']
    inlines = [DocumentSubSectionInline]

@admin.register(Document)
class DocumentAdmin(VersionAdmin, NestedModelAdmin):
    form = DocumentForm
    list_display = ['title', 'version', 'created_by', 'created_at']
    list_filter = ['department', 'tags', 'created_at']
    search_fields = ['title', 'slug']
    readonly_fields = ['created_at']
    filter_horizontal = ['tags']
    prepopulated_fields = {'slug': ('title',)}
    inlines = [DocumentSectionInline]
    actions = ['compile_pdf', 'convert_to_html']

    @admin.action(description="Compile selected documents to PDF")
    def compile_pdf(self, request, queryset):

        def compile_one(document):
            compiler = LatexCompiler(document)
            pdf_path = compiler.generate_pdf()

            if not pdf_path:
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

    @admin.action(description="🔁 Convert LaTeX to HTML (generate HTMLFile)")
    def convert_to_html(self, request, queryset):

        def convert_one(document):
            html_content = LatexDocumentConverter(document).to_html()

            # Remove existing HTMLFile if present
            HTMLFile.objects.filter(document=document).delete()

            # Create new HTMLFile entry with raw HTML content only
            HTMLFile.objects.create(
                document=document,
                preset=None,  # or assign a default preset if needed
                content=html_content
            )

            return document

        result = BatchAction(queryset).run(convert_one)
        BatchAction.display_messages(result, self.message_user, request, verb="convert")


# ────────────────────────────────────────────────
# 📎 Output Files Admin
# ────────────────────────────────────────────────

@admin.register(PDFFile)
class PDFFileAdmin(admin.ModelAdmin):
    list_display = ['document', 'created_at']
    readonly_fields = ['created_at']
    search_fields = ['document__title']
    list_filter = ['created_at']
    ordering = ['-created_at']


class HTMLFileForm(forms.ModelForm):
    class Meta:
        model = HTMLFile
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['content'].widget = TipTapWidget()


@admin.register(HTMLFile)
class HTMLFileAdmin(admin.ModelAdmin):
    form = HTMLFileForm
    list_display = ['document', 'created_at']
    readonly_fields = ['created_at']
    search_fields = ['document__title']
    list_filter = ['created_at']
    ordering = ['-created_at']

