from django import forms
from django.contrib import admin
from django_ace import AceWidget
from django_tiptap.widgets import TipTapWidget
from django.core.files import File
from django.utils.text import slugify
import os

from reversion.admin import VersionAdmin
from polymorphic.admin import (
    PolymorphicParentModelAdmin,
    PolymorphicChildModelAdmin,
    PolymorphicChildModelFilter
)

from .pdf import LaTeXToPDFConverter
from .models import (
    Tag, Department,
    LatexPreset, HTMLPreset,
    Document, LatexDocument, HTMLDocument,
    PDFFile
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
        #self.fields['summary'].widget = AceWidget(mode='latex', theme='chrome')
        self.fields['content'].widget = AceWidget(mode='latex', theme='chrome')


class HTMLWidgetMixin:
    def configure_widgets(self):
        # self.fields['summary'].widget = TipTapWidget()
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
    search_fields = ['title', 'summary', 'slug']
    readonly_fields = ['created_at']
    filter_horizontal = ['tags']
    prepopulated_fields = {'slug': ('title',)}

    @admin.display(description="Type")
    def get_type(self, obj):
        return obj.get_real_instance_class().__name__

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
                converter = LaTeXToPDFConverter(document)
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
