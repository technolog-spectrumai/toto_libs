from django import forms
from django.contrib import admin
from nested_admin import NestedModelAdmin, NestedStackedInline, NestedTabularInline
from adminsortable2.admin import SortableAdminMixin
from django_tiptap.widgets import TipTapWidget
from .models import Site, Tag, Article, Section, SubSection, Image, Menu, Category, CategoryLink, Theme, Font


@admin.register(Font)
class FontAdmin(admin.ModelAdmin):
    list_display = ('name', 'import_url', 'fallback')
    search_fields = ('name',)
    ordering = ('name',)


@admin.register(Theme)
class ThemeAdmin(admin.ModelAdmin):
    list_display = ('name', 'heading_font', 'body_font')
    search_fields = ('name',)
    ordering = ('name',)
    fieldsets = (
        ('General', {
            'fields': ('name', 'heading_font', 'body_font')
        }),
        ('Light Mode Colors', {
            'classes': ('collapse',),
            'fields': (
                'light_bg_top', 'light_bg_bottom',
                'light_text_main', 'light_text_muted',
                'light_accent', 'light_border',
                'light_card_bg', 'light_footer_bg',
                'light_nav_bg_start', 'light_nav_bg_end',
                'light_nav_text', 'light_nav_shadow',
            )
        }),
        ('Dark Mode Colors', {
            'classes': ('collapse',),
            'fields': (
                'dark_bg_top', 'dark_bg_bottom',
                'dark_text_main', 'dark_text_muted',
                'dark_accent', 'dark_border',
                'dark_card_bg', 'dark_footer_bg',
                'dark_nav_bg_start', 'dark_nav_bg_end',
                'dark_nav_text', 'dark_nav_shadow',
            )
        }),
    )


admin.site.register(Tag)


class CategoryLinkInline(NestedTabularInline):
    model = CategoryLink
    extra = 1
    fields = ['category', 'order']
    autocomplete_fields = ['category']
    ordering = ['order']


class MenuInline(NestedStackedInline):
    model = Menu
    extra = 1
    fields = ['title', 'slug', 'order', 'category_link']
    show_change_link = True
    inlines = [CategoryLinkInline]


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug']
    search_fields = ['name', 'slug']


@admin.register(Menu)
class MenuAdmin(SortableAdminMixin, admin.ModelAdmin):
    list_display = ['title', 'slug', 'site', 'order']
    list_filter = ['site']
    search_fields = ['title', 'slug']
    prepopulated_fields = {'slug': ('title',)}
    inlines = [CategoryLinkInline]
    ordering = ['order']


@admin.register(CategoryLink)
class CategoryLinkAdmin(SortableAdminMixin, admin.ModelAdmin):
    list_display = ['category', 'menu', 'order']
    list_filter = ['menu']
    autocomplete_fields = ['category']
    ordering = ['order']


@admin.register(Site)
class SiteAdmin(NestedModelAdmin):
    list_display = ['name', 'slug', 'domain', 'creation_year', 'active', 'owner', 'created_at', 'theme']
    list_filter = ['active', 'creation_year']
    search_fields = ['name', 'slug', 'domain']
    prepopulated_fields = {'slug': ('name',)}
    inlines = [MenuInline]

    fieldsets = (
        ('Configuration', {
            'fields': ('description', 'active', 'owner', 'slug', 'theme'),
            'description': "General site settings and ownership."
        }),
        ('Header', {
            'fields': ('name', 'domain', 'head_slogan', 'banner_image'),
            'description': "Site identity and header branding."
        }),
        ('Footer', {
            'fields': ('creation_year', 'foot_slogan', 'footer_about', 'created_at'),
            'description': "Customize the footer messaging and metadata."
        }),
    )
    readonly_fields = ['created_at']


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


@admin.register(Section)
class SectionAdmin(SortableAdminMixin, admin.ModelAdmin):
    form = SectionForm
    list_display = ['article', 'order', 'heading', 'created_at']
    list_filter = ['article']
    search_fields = ['heading', 'content']


@admin.register(SubSection)
class SubSectionAdmin(SortableAdminMixin, admin.ModelAdmin):
    form = SubSectionForm
    list_display = ['section', 'order', 'title', 'created_at']
    list_filter = ['section']
    search_fields = ['title', 'content']


@admin.register(Image)
class ImageAdmin(admin.ModelAdmin):
    list_display = ['title', 'description', 'uploaded_at']
    search_fields = ['title', 'description']
    list_filter = ['uploaded_at']

