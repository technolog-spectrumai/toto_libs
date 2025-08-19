from django.contrib import admin
from .models import MemoDeck, MemoCard, InfoTag

@admin.register(InfoTag)
class TagAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)
    ordering = ('name',)


@admin.register(MemoDeck)
class MemoDeckAdmin(admin.ModelAdmin):
    list_display = ('title', 'author', 'created_at', 'tag_list')
    search_fields = ('title', 'description', 'author__username', 'tags__name')
    list_filter = ('created_at', 'tags')
    ordering = ('-created_at',)
    autocomplete_fields = ('author', 'tags')
    readonly_fields = ('created_at',)

    def tag_list(self, obj):
        return ", ".join(tag.name for tag in obj.tags.all())
    tag_list.short_description = "Tags"


@admin.register(MemoCard)
class MemoCardAdmin(admin.ModelAdmin):
    list_display = ('title', 'deck', 'deck_author', 'has_mermaid_code')
    search_fields = ('title', 'content', 'deck__title', 'deck__author__username')
    list_filter = ('deck__title',)
    autocomplete_fields = ('deck',)

    def deck_author(self, obj):
        return obj.deck.author.username
    deck_author.short_description = "Deck Author"

    def has_mermaid_code(self, obj):
        return bool(obj.mermaid_code.strip())
    has_mermaid_code.boolean = True
    has_mermaid_code.short_description = "Mermaid Code?"
