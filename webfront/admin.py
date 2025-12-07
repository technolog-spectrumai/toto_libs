from django.contrib import admin
from django import forms
from django.utils.html import format_html
from django_ace import AceWidget
from .models import StaticPage, HtmlTemplate, DynamicPage


# -----------------------------
# Forms with Ace editor
# -----------------------------
class StaticPageForm(forms.ModelForm):
    class Meta:
        model = StaticPage
        fields = ['slug', 'title', 'body']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['body'].widget = AceWidget(
            mode='html',
            theme='chrome',
            width="100%",
            height="400px",
            showprintmargin=False
        )


class HtmlTemplateForm(forms.ModelForm):
    class Meta:
        model = HtmlTemplate
        fields = ['name', 'content', 'json_schema']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # HTML editor for template content
        self.fields['content'].widget = AceWidget(
            mode='html',
            theme='chrome',
            width="100%",
            height="400px",
            showprintmargin=False
        )
        # JSON editor for schema
        self.fields['json_schema'].widget = AceWidget(
            mode='json',
            theme='chrome',
            width="100%",
            height="300px",
            showprintmargin=False
        )


class DynamicPageForm(forms.ModelForm):
    class Meta:
        model = DynamicPage
        fields = ['slug', 'title', 'data', 'template']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Use Ace editor for JSON data field
        self.fields['data'].widget = AceWidget(
            mode='json',
            theme='chrome',
            width="100%",
            height="400px",
            showprintmargin=False
        )


# -----------------------------
# Admins
# -----------------------------
@admin.register(StaticPage)
class StaticPageAdmin(admin.ModelAdmin):
    form = StaticPageForm
    list_display = ('title', 'slug', 'created_at', 'view_link')
    search_fields = ('title', 'slug')
    prepopulated_fields = {"slug": ("title",)}
    ordering = ('-created_at',)

    def view_link(self, obj):
        return format_html(
            '<a href="{}" target="_blank">🔗 View</a>',
            obj.get_absolute_url()
        )
    view_link.short_description = "Page Link"


@admin.register(HtmlTemplate)
class HtmlTemplateAdmin(admin.ModelAdmin):
    form = HtmlTemplateForm
    list_display = ('name', 'created_at')
    search_fields = ('name',)
    ordering = ('-created_at',)


@admin.register(DynamicPage)
class DynamicPageAdmin(admin.ModelAdmin):
    form = DynamicPageForm
    list_display = ('title', 'slug', 'template', 'created_at', 'view_link')
    search_fields = ('title', 'slug')
    prepopulated_fields = {"slug": ("title",)}
    ordering = ('-created_at',)

    def view_link(self, obj):
        return format_html(
            '<a href="{}" target="_blank">🔗 View</a>',
            obj.get_absolute_url()
        )
    view_link.short_description = "Page Link"
