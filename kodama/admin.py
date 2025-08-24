from django import forms
from django.contrib import admin
from nested_admin import NestedModelAdmin, NestedStackedInline
from adminsortable2.admin import SortableAdminMixin
from django_tiptap.widgets import TipTapWidget
from .models import Site, Tag, Article, Section, SubSection, Image

# 🔖 Tags
admin.site.register(Tag)

# Site Admin
@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'domain', 'creation_year', 'active', 'owner', 'created_at']
    list_filter = ['active', 'creation_year']
    search_fields = ['name', 'slug', 'domain']
    prepopulated_fields = {'slug': ('name',)}

# ✏Forms
class SectionForm(forms.ModelForm):
    content = forms.CharField(widget=TipTapWidget())

    class Meta:
        model = Section
        fields = '__all__'

class SubSectionForm(forms.ModelForm):
    content = forms.CharField(widget=TipTapWidget())

    class Meta:
        model = SubSection
        fields = '__all__'

# Inlines
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

# Article Admin
@admin.register(Article)
class ArticleAdmin(NestedModelAdmin):
    list_display = ['title', 'site', 'author', 'created_at']
    list_filter = ['site', 'tags', 'created_at']
    search_fields = ['title', 'summary', 'slug']
    readonly_fields = ['created_at']
    date_hierarchy = 'created_at'
    prepopulated_fields = {'slug': ('title',)}
    filter_horizontal = ['tags']
    inlines = [SectionInline]

# Section Admin (optional table view)
@admin.register(Section)
class SectionAdmin(SortableAdminMixin, admin.ModelAdmin):
    form = SectionForm
    list_display = ['article', 'order', 'heading', 'created_at']
    list_filter = ['article']
    search_fields = ['heading', 'content']

# SubSection Admin (optional table view)
@admin.register(SubSection)
class SubSectionAdmin(SortableAdminMixin, admin.ModelAdmin):
    form = SubSectionForm
    list_display = ['section', 'order', 'title', 'created_at']
    list_filter = ['section']
    search_fields = ['title', 'content']

# 🖼Image Admin
@admin.register(Image)
class ImageAdmin(admin.ModelAdmin):
    list_display = ['title', 'description', 'uploaded_at']
    search_fields = ['title', 'description']
    list_filter = ['uploaded_at']
