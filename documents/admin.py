from django.contrib import admin
from django import forms
from django_tiptap.widgets import TipTapWidget
from .models import Tag, Department, Document, Section

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


if SHOW_INLINE:
    class SectionInline(admin.StackedInline):  # Use StackedInline for better TipTap layout
        model = Section
        form = SectionInlineForm
        extra = 1
        ordering = ['order']
else:
    SectionInline = None


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ['title', 'type', 'status', 'department', 'author', 'created_at']
    list_filter = ['type', 'status', 'department', 'tags']
    search_fields = ['title', 'summary', 'slug']
    readonly_fields = ['created_at']
    date_hierarchy = 'created_at'
    prepopulated_fields = {'slug': ('title',)}
    filter_horizontal = ['tags']
    inlines = [inline for inline in [SectionInline] if inline]


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    form = SectionAdminForm
    list_display = ['report', 'order', 'heading', 'created_at']
    list_filter = ['report', 'created_at']
    search_fields = ['heading', 'content']
