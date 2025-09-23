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
    PDFFile
)
from .convert import LatexToHTMLConverter, HTMLToLatexConverter
from .batch import BatchAction
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

def resolve_editor_widget(preset_type):
    if preset_type == "HTML":
        return TipTapWidget()
    return AceWidget(mode='latex', theme='chrome')


class DocumentForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        preset_type = self.instance.preset_type if self.instance.pk else "LaTeX"
        widget = resolve_editor_widget(preset_type)
        self.fields['content'].widget = widget
        self.fields['content'].widget.attrs.update({'style': 'font-family: monospace;'})


class DocumentSectionForm(forms.ModelForm):
    class Meta:
        model = DocumentSection
        fields = ['title', 'order', 'content']

    def __init__(self, *args, **kwargs):
        document = kwargs.pop('document', None)
        super().__init__(*args, **kwargs)
        preset_type = "LaTeX"
        if document and hasattr(document, 'preset_type'):
            preset_type = document.preset_type
        widget = resolve_editor_widget(preset_type)
        self.fields['content'].widget = widget
        self.fields['content'].widget.attrs.update({'style': 'font-family: monospace;'})


class DocumentSubSectionForm(forms.ModelForm):
    class Meta:
        model = DocumentSubSection
        fields = ['title', 'order', 'content']

    def __init__(self, *args, **kwargs):
        document = kwargs.pop('document', None)
        super().__init__(*args, **kwargs)
        preset_type = "LaTeX"
        if document and hasattr(document, 'preset_type'):
            preset_type = document.preset_type
        widget = resolve_editor_widget(preset_type)
        self.fields['content'].widget = widget
        self.fields['content'].widget.attrs.update({'style': 'font-family: monospace;'})



class DocumentSubSectionInline(NestedStackedInline):
    model = DocumentSubSection
    form = DocumentSubSectionForm
    extra = 0
    ordering = ['order']

    def get_formset(self, request, obj=None, **kwargs):
        FormSet = super().get_formset(request, obj, **kwargs)

        class CustomFormSet(FormSet):
            def __init__(self, *args, **kwargs):
                kwargs['form_kwargs'] = {'document': obj.document if obj else None}
                super().__init__(*args, **kwargs)

        return CustomFormSet


class DocumentSectionInline(NestedStackedInline):
    model = DocumentSection
    form = DocumentSectionForm
    extra = 0
    ordering = ['order']
    inlines = [DocumentSubSectionInline]

    def get_formset(self, request, obj=None, **kwargs):
        FormSet = super().get_formset(request, obj, **kwargs)

        class CustomFormSet(FormSet):
            def __init__(self, *args, **kwargs):
                kwargs['form_kwargs'] = {'document': obj}
                super().__init__(*args, **kwargs)

        return CustomFormSet


@admin.register(Document)
class DocumentAdmin(VersionAdmin, NestedModelAdmin):
    form = DocumentForm
    list_display = ['title', 'version', 'created_by', 'created_at', 'preset_type']
    list_filter = ['department', 'tags', 'created_at']
    search_fields = ['title', 'slug']
    readonly_fields = ['created_at']
    filter_horizontal = ['tags']
    prepopulated_fields = {'slug': ('title',)}
    inlines = [DocumentSectionInline]
    actions = ['compile_pdf', 'convert_to_html', 'convert_to_latex']
    #
    # @admin.display(description="Preset Type")
    # def preset_type(self, obj):
    #     return obj.preset_type

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

    @admin.action(description="🔁 Convert LaTeX → HTML (in-place)")
    def convert_to_html(self, request, queryset):
        def convert_one(document):
            if not isinstance(document.preset, LatexPreset):
                return f"Skipped: {document.title} is not LaTeX"

            # Convert main content
            document.content = LatexToHTMLConverter(document.content).convert()
            document.save()

            # Convert sections
            for section in document.sections.all():
                section.content = LatexToHTMLConverter(section.content).convert()
                section.save()

                # Convert subsections
                for subsection in section.subsections.all():
                    subsection.content = LatexToHTMLConverter(subsection.content).convert()
                    subsection.save()

            return document

        result = BatchAction(queryset).run(convert_one)
        BatchAction.display_messages(result, self.message_user, request, verb="convert")

    @admin.action(description="🔁 Convert HTML → LaTeX (in-place)")
    def convert_to_latex(self, request, queryset):
        def convert_one(document):
            if not isinstance(document.preset, HTMLPreset):
                return f"Skipped: {document.title} is not HTML"

            # Convert main content
            document.content = HTMLToLatexConverter(document.content).convert()
            document.save()

            # Convert sections
            for section in document.sections.all():
                section.content = HTMLToLatexConverter(section.content).convert()
                section.save()

                # Convert subsections
                for subsection in section.subsections.all():
                    subsection.content = HTMLToLatexConverter(subsection.content).convert()
                    subsection.save()

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