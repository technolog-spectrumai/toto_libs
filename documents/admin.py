from django.contrib import admin
from django import forms
from django.utils.text import slugify
from django.core.files import File
from nested_admin import NestedModelAdmin, NestedStackedInline
from reversion.admin import VersionAdmin
from .pdf import LatexCompiler
from .models import (
    Tag, Department,
    LatexPreset,
    Document, DocumentSection, DocumentSubSection
)
from .convert import LatexToHTMLConverter, HTMLToLatexConverter
from .batch import BatchAction
from django_ace import AceWidget
from django_tiptap.widgets import TipTapWidget
from vault.models import VaultFile


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


# ────────────────────────────────────────────────
# 🧩 Document Admin
# ────────────────────────────────────────────────



class DocumentSectionForm(forms.ModelForm):
    class Meta:
        model = DocumentSection
        fields = ['title', 'order', 'is_raw', 'content']

    def __init__(self, *args, **kwargs):
        document = kwargs.pop('document', None)
        super().__init__(*args, **kwargs)

        if self.instance.is_raw:
            widget = AceWidget(mode='latex', theme='chrome')
        else:
            widget = TipTapWidget()

        self.fields['content'].widget = widget
        self.fields['content'].widget.attrs.update({'style': 'font-family: monospace;'})


class DocumentSubSectionForm(forms.ModelForm):
    class Meta:
        model = DocumentSubSection
        fields = ['title', 'order', 'is_raw', 'content']

    def __init__(self, *args, **kwargs):
        document = kwargs.pop('document', None)
        super().__init__(*args, **kwargs)

        if self.instance.is_raw:
            widget = AceWidget(mode='latex', theme='chrome')
        else:
            widget = TipTapWidget()

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
    list_display = ['title', 'version', 'created_by', 'created_at']
    list_filter = ['department', 'tags', 'created_at']
    search_fields = ['title', 'slug']
    readonly_fields = ['created_at']
    filter_horizontal = ['tags']
    prepopulated_fields = {'slug': ('title',)}
    inlines = [DocumentSectionInline]
    actions = ['compile_pdf']

    @admin.action(description="Compile selected documents to PDF and send to Vault")
    def compile_pdf(self, request, queryset):

        def compile_one(document):
            # Force all sections and subsections to save as raw (LaTeX)
            for section in document.sections.all():
                section.is_raw = True
                section.save()

                for subsection in section.subsections.all():
                    subsection.is_raw = True
                    subsection.save()

            # Compile PDF
            compiler = LatexCompiler(document)
            pdf_path = compiler.generate_pdf()

            if not pdf_path:
                raise FileNotFoundError("PDF file not found after generation.")

            # Save directly to Vault
            with open(pdf_path, 'rb') as f:
                VaultFile.objects.create(
                    owner=document.created_by,
                    title=document.title,
                    file=File(f, name=f"{slugify(document.title)}.pdf"),
                    file_type='pdf',
                    notes=f"Compiled from document ID {document.id}"
                )

            return document

        result = BatchAction(queryset).run(compile_one)
        BatchAction.display_messages(result, self.message_user, request, verb="compile and vault")

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)

        for obj in instances:
            if hasattr(obj, 'is_raw') and hasattr(obj, 'content'):
                if obj.pk:
                    old = obj.__class__.objects.get(pk=obj.pk)
                    if old.is_raw != obj.is_raw:
                        if obj.is_raw:
                            obj.content = HTMLToLatexConverter(obj.content).convert()
                        else:
                            obj.content = LatexToHTMLConverter(obj.content).convert()
            obj.save()

        formset.save_m2m()
