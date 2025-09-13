from django import forms
from django.contrib import admin
from nested_admin import NestedModelAdmin, NestedStackedInline
from django_tiptap.widgets import TipTapWidget
from django_ace import AceWidget

from .models import (
    Tag, Department,
    LatexPreset, HTMLPreset,
    LatexDocument, LatexSection, LatexSubSection,
    HTMLDocument, HTMLSection, HTMLSubSection
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

    def get_inline_instances(self, request, obj=None):
        return [LatexSectionInline(self.model, self.admin_site)] if obj and obj.deep else []

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
        'title', 'slug', 'version', 'created_by', 'created_at', 'deep', 'linked_latex'
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

    def get_inline_instances(self, request, obj=None):
        return [HTMLSectionInline(self.model, self.admin_site)] if obj and obj.deep else []


@admin.register(HTMLSection)
class HTMLSectionAdmin(admin.ModelAdmin):
    form = HTMLSectionForm
    list_display = ['title', 'document', 'order', 'deep']
    list_filter = ['document']
    search_fields = ['title', 'content']
    ordering = ['document', 'order']


@admin.register(HTMLSubSection)
class HTMLSubSectionAdmin(admin.ModelAdmin):
    form = HTMLSubSectionForm
    list_display = ['title', 'section', 'order']
    list_filter = ['section__document']
    search_fields = ['title', 'content']
    ordering = ['section__document', 'section', 'order']
