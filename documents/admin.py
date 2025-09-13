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


from .models import (
    Tag, Department,
    LatexPreset, HTMLPreset,
    LatexDocument, LatexSection, LatexSubSection,
    HTMLDocument, HTMLSection, HTMLSubSection, PDFFile
)

# ────────────────────────────────────────────────
# 🔖 Basic Admins
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


@admin.register(LatexPreset)
class LatexPresetAdmin(admin.ModelAdmin):
    list_display = ['name', 'document_class']
    search_fields = ['name', 'document_class']


@admin.register(HTMLPreset)
class HTMLPresetAdmin(admin.ModelAdmin):
    list_display = ['name', 'template_name']
    search_fields = ['name', 'template_name']

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
class LatexDocumentAdmin(NestedModelAdmin):
    form = LatexDocumentForm
    list_display = ['title', 'slug', 'version', 'created_by', 'created_at', 'deep']
    list_filter = ['department', 'tags', 'created_at']
    search_fields = ['title', 'summary', 'version', 'slug']
    filter_horizontal = ['tags']
    readonly_fields = ['created_at']
    prepopulated_fields = {'slug': ('title',)}
    actions = ["compile_pdf"]

    def get_inline_instances(self, request, obj=None):
        return [LatexSectionInline(self.model, self.admin_site)] if obj and obj.deep else []

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

@admin.register(LatexSection)
class LatexSectionAdmin(admin.ModelAdmin):
    form = LatexSectionForm
    list_display = ['title', 'document', 'order', 'deep']
    list_filter = ['document']
    search_fields = ['title', 'content']
    ordering = ['document', 'order']


@admin.register(LatexSubSection)
class LatexSubSectionAdmin(admin.ModelAdmin):
    form = LatexSubSectionForm
    list_display = ['title', 'section', 'order']
    list_filter = ['section__document']
    search_fields = ['title', 'content']
    ordering = ['section__document', 'section', 'order']

# ────────────────────────────────────────────────
# 🌐 HTML Admin
# ────────────────────────────────────────────────

class HTMLDocumentForm(forms.ModelForm):
    class Meta:
        model = HTMLDocument
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['summary'].widget = TipTapWidget()


class HTMLSubSectionForm(forms.ModelForm, HTMLWidgetMixin):
    class Meta:
        model = HTMLSubSection
        fields = ['title', 'order', 'section', 'content']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.configure_widgets()


class HTMLSectionForm(forms.ModelForm, HTMLWidgetMixin):
    class Meta:
        model = HTMLSection
        fields = ['title', 'order', 'document', 'content']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.configure_widgets()


class HTMLSubSectionInline(NestedStackedInline):
    model = HTMLSubSection
    form = HTMLSubSectionForm
    extra = 1
    ordering = ['order']


class HTMLSectionInline(NestedStackedInline):
    model = HTMLSection
    form = HTMLSectionForm
    extra = 1
    ordering = ['order']
    inlines = [HTMLSubSectionInline]


@admin.register(HTMLDocument)
class HTMLDocumentAdmin(NestedModelAdmin):
    form = HTMLDocumentForm
    list_display = [
        'title', 'slug', 'version', 'created_by', 'created_at', 'is_deep', 'linked_latex'
    ]
    list_filter = ['department', 'tags', 'created_at']
    search_fields = ['title', 'summary', 'version', 'slug', 'linked_latex__title']
    filter_horizontal = ['tags']
    readonly_fields = ['created_at']
    prepopulated_fields = {'slug': ('title',)}

    fieldsets = (
        (None, {
            'fields': [
                'title', 'slug', 'summary', 'version',
                'created_by', 'department', 'tags', 'preset', 'linked_latex'
            ]
        }),
        ('Metadata', {
            'fields': ['created_at']
        }),
    )

    @admin.display(boolean=True, description="Has Sections")
    def is_deep(self, obj):
        return obj.deep

    def get_inline_instances(self, request, obj=None):
        return [HTMLSectionInline(self.model, self.admin_site)] if obj and obj.deep else []


@admin.register(HTMLSection)
class HTMLSectionAdmin(admin.ModelAdmin):
    form = HTMLSectionForm
    list_display = ['title', 'document', 'order', 'is_deep']
    list_filter = ['document']
    search_fields = ['title', 'content']
    ordering = ['document', 'order']

    @admin.display(boolean=True, description="Has Subsections")
    def is_deep(self, obj):
        return obj.deep


@admin.register(HTMLSubSection)
class HTMLSubSectionAdmin(admin.ModelAdmin):
    form = HTMLSubSectionForm
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



