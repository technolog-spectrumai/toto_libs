import os

from django.contrib import admin
from django import forms
from django.utils.text import slugify
from django.core.files import File
from nested_admin import NestedModelAdmin, NestedStackedInline
from reversion.admin import VersionAdmin
from .compiler import LatexCompiler
from .models import (
    Tag, Department,
    LatexPreset, Image,
    Document, DocumentSection, DocumentSubSection
)
from .convert import LatexToHTMLConverter, HTMLToLatexConverter
from .batch import BatchAction
from django_ace import AceWidget
from django_tiptap.widgets import TipTapWidget
from vault.models import VaultFile
from django.utils.html import format_html
from latextile.models import LatexProject, TexFile


# ────────────────────────────────────────────────
# 🔖 Tag & Department Admin
# ────────────────────────────────────────────────

@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    search_fields = ['name']
    list_display = ['name']


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ['name', 'owner', 'bucket']
    search_fields = ['name']
    list_filter = ['owner']
    readonly_fields = ['owner']
    fieldsets = (
        (None, {'fields': ['name', 'owner', 'seal', 'bucket']}),
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
        fields = ['title', 'order', 'is_raw', 'content', 'image']

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
    actions = ['export_tex']

    @admin.action(description="Export selected documents to .tex")
    def export_tex(self, request, queryset):

        def export_one(document):
            # Force all sections and subsections to save as raw (LaTeX)
            for section in document.sections.all():
                section.is_raw = True
                section.save()

                for subsection in section.subsections.all():
                    subsection.is_raw = True
                    subsection.save()

            # Generate .tex file
            compiler = LatexCompiler(document)
            tex_path = compiler.to_tex()

            if not tex_path or not os.path.exists(tex_path):
                raise FileNotFoundError("TeX file not found after generation.")

            # Require department bucket
            if not document.department or not document.department.bucket:
                raise ValueError(f"Document '{document.title}' has no department bucket assigned.")

            bucket = document.department.bucket

            # Create or get LatexProject
            project, _ = LatexProject.objects.get_or_create(
                user=document.created_by,
                name=f"{document.title} (auto-export)",
                bucket=bucket
            )

            # Save TexFile
            with open(tex_path, 'rb') as f:
                TexFile.objects.create(
                    project=project,
                    filename=os.path.basename(tex_path),
                    file=File(f, name=os.path.basename(tex_path))
                )

            return document

        result = BatchAction(queryset).run(export_one)
        BatchAction.display_messages(result, self.message_user, request, verb="export to LaTeX project")

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

    @admin.register(Image)
    class ImageAdmin(admin.ModelAdmin):
        list_display = ['id', 'caption', 'preview']
        search_fields = ['caption']
        readonly_fields = ['preview']
        fields = ['file', 'caption', 'preview']

        def preview(self, obj):
            if obj.file:
                return format_html('<img src="{}" style="max-width: 300px; max-height: 200px;" />', obj.file.url)
            return "No image"
        preview.short_description = "Image Preview"
