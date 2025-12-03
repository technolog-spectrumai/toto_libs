from django.contrib import admin
from django import forms
from django_ace import AceWidget
from .models import Page


class PageForm(forms.ModelForm):
    class Meta:
        model = Page
        fields = ['slug', 'title', 'body']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Use Ace editor for the body field
        self.fields['body'].widget = AceWidget(
            mode='html',        # syntax highlighting mode
            theme='chrome',     # editor theme (others: monokai, github, twilight, etc.)
            width="100%",       # editor width
            height="400px",     # editor height
            showprintmargin=False
        )


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    form = PageForm
    list_display = ('title', 'slug', 'created_at')
    search_fields = ('title', 'slug')
    prepopulated_fields = {"slug": ("title",)}  # auto-fill slug from title
    ordering = ('-created_at',)
