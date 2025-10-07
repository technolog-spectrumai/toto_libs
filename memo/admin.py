from django.contrib import admin
from .models import MemoDeck, MemoCard, Tag
from adminsortable2.admin import SortableAdminMixin

@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)
    ordering = ('name',)


class MemoCardInline(admin.TabularInline):
    model = MemoCard
    extra = 1
    ordering = ['order']


@admin.register(MemoDeck)
class MemoDeckAdmin(admin.ModelAdmin):
    list_display = ('title', 'author', 'created_at', 'tag_list')
    search_fields = ('title', 'description', 'author__username', 'tags__name')
    list_filter = ('created_at', 'tags')
    ordering = ('-created_at',)
    autocomplete_fields = ('author', 'tags')
    readonly_fields = ('created_at',)
    inlines = [MemoCardInline]

    def tag_list(self, obj):
        return ", ".join(tag.name for tag in obj.tags.all())
    tag_list.short_description = "Tags"


@admin.register(MemoCard)
class MemoCardAdmin(SortableAdminMixin, admin.ModelAdmin):
    list_display = ('title', 'deck', 'deck_author', 'order')
    list_editable = ('order',)
    search_fields = ('title', 'content', 'deck__title', 'deck__author__username')
    list_filter = ('deck__title',)
    autocomplete_fields = ('deck',)

    def deck_author(self, obj):
        return obj.deck.author.username
    deck_author.short_description = "Deck Author"

