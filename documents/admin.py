from django import forms
from django.contrib import admin
from nested_admin import NestedModelAdmin, NestedStackedInline
from django_tiptap.widgets import TipTapWidget
from django_ace import AceWidget


from .models import (
    Tag, Department,
    LatexPreset, HTMLPreset,
    Document, Section, SubSection
)


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


class DocumentForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = kwargs.get('instance')

        if instance:
            self.fields['latex_summary'].widget = AceWidget(mode='latex', theme='chrome')
            self.fields['html_summary'].widget = TipTapWidget()


class ContentWidgetMixin:
    def configure_widgets(self):
        doc = getattr(self.instance, 'document', None) or getattr(self.instance, 'section', None)
        if doc and hasattr(doc, 'document'):
            doc = doc.document  # for SubSection

        if doc and getattr(self.instance, 'deep', False):
            self.fields['latex_content'].widget = (
                AceWidget(mode='latex', theme='chrome') if doc.latex_preset else forms.HiddenInput()
            )
            self.fields['html_content'].widget = (
                TipTapWidget() if doc.html_preset else forms.HiddenInput()
            )
        else:
            self.fields['latex_content'].widget = forms.HiddenInput()
            self.fields['html_content'].widget = forms.HiddenInput()


class SectionForm(forms.ModelForm, ContentWidgetMixin):
    class Meta:
        model = Section
        fields = ['title', 'order', 'document', 'latex_content', 'html_content']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.configure_widgets()


class SubSectionForm(forms.ModelForm, ContentWidgetMixin):
    class Meta:
        model = SubSection
        fields = ['title', 'order', 'section', 'latex_content', 'html_content']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.configure_widgets()


class SubSectionInline(NestedStackedInline):
    model = SubSection
    form = SubSectionForm
    extra = 1
    ordering = ['order']


class SectionInline(NestedStackedInline):
    model = Section
    form = SectionForm
    extra = 1
    ordering = ['order']
    inlines = [SubSectionInline]


@admin.register(Document)
class DocumentAdmin(NestedModelAdmin):
    form = DocumentForm
    list_display = ['title', 'slug', 'version', 'created_by', 'created_at', 'deep']
    list_filter = ['department', 'tags', 'created_at']
    search_fields = ['title', 'html_summary', 'latex_summary', 'version', 'slug']
    filter_horizontal = ['tags']
    readonly_fields = ['created_at']
    prepopulated_fields = {'slug': ('title',)}

    def get_inline_instances(self, request, obj=None):
        if not obj or not obj.deep:
            return []
        return [SectionInline(self.model, self.admin_site)]

    def get_fields(self, request, obj=None):
        return [
            'title', 'slug', 'html_summary', 'latex_summary', 'version',
            'created_by', 'tags', 'department', 'created_at',
            'latex_preset', 'html_preset'
        ]


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    form = SectionForm
    list_display = ['title', 'document', 'order', 'deep']
    list_filter = ['document']
    search_fields = ['title', 'latex_content', 'html_content']
    ordering = ['document', 'order']


@admin.register(SubSection)
class SubSectionAdmin(admin.ModelAdmin):
    form = SubSectionForm
    list_display = ['title', 'section', 'order']
    list_filter = ['section__document']
    search_fields = ['title', 'latex_content', 'html_content']
    ordering = ['section__document', 'section', 'order']
