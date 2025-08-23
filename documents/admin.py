from django.contrib import admin
from django import forms
from django_tiptap.widgets import TipTapWidget
from nested_admin import NestedModelAdmin, NestedStackedInline
from .models import Tag, Department, Document, Section, SubSection

SHOW_INLINE = True
SHOW_TABLE = True

# 🏷️ Tags
admin.site.register(Tag)

# 🏢 Departments
@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ['name', 'owner']
    search_fields = ['name']
    list_filter = ['owner']


# ✏️ Forms
class SectionAdminForm(forms.ModelForm):
    content = forms.CharField(widget=TipTapWidget())

    class Meta:
        model = Section
        fields = '__all__'


class SectionInlineForm(forms.ModelForm):
    content = forms.CharField(widget=TipTapWidget())

    class Meta:
        model = Section
        fields = '__all__'


class SubSectionInlineForm(forms.ModelForm):
    content = forms.CharField(widget=TipTapWidget())

    class Meta:
        model = SubSection
        fields = '__all__'


# 📑 Inlines
class SubSectionInline(NestedStackedInline):
    model = SubSection
    form = SubSectionInlineForm
    extra = 1
    ordering = ['order']


if SHOW_INLINE:
    class SectionInline(NestedStackedInline):  # Use NestedStackedInline for nesting
        model = Section
        form = SectionInlineForm
        extra = 1
        ordering = ['order']
        inlines = [SubSectionInline]
else:
    SectionInline = None


# 📄 Document Admin
@admin.register(Document)
class DocumentAdmin(NestedModelAdmin):
    list_display = ['title', 'type', 'status', 'department', 'author', 'created_at']
    list_filter = ['type', 'status', 'department', 'tags']
    search_fields = ['title', 'summary', 'slug']
    readonly_fields = ['created_at']
    date_hierarchy = 'created_at'
    prepopulated_fields = {'slug': ('title',)}
    filter_horizontal = ['tags']
    inlines = [inline for inline in [SectionInline] if inline]


# 📚 Section Admin
@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    form = SectionAdminForm
    list_display = ['report', 'order', 'heading', 'created_at']
    list_filter = ['report', 'created_at']
    search_fields = ['heading', 'content']


# 📘 SubSection Admin (optional)
@admin.register(SubSection)
class SubSectionAdmin(admin.ModelAdmin):
    form = SubSectionInlineForm
    list_display = ['section', 'order', 'title', 'created_at']
    list_filter = ['section']
    search_fields = ['title', 'content']
