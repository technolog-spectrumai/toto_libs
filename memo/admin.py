import datetime
import json

from django.contrib import admin
from adminsortable2.admin import SortableAdminMixin
from django.http import HttpResponse
from django.template.defaultfilters import slugify

from .models import MemoDeck, MemoCard, Tag
from vault.models import VaultFile
from .batch import BatchAction

# ────────────────────────────────────────────────
# 🔖 Tag Admin
# ────────────────────────────────────────────────

@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)
    ordering = ('name',)

# ────────────────────────────────────────────────
# 🧩 MemoCard Inline
# ────────────────────────────────────────────────

class MemoCardInline(admin.TabularInline):
    model = MemoCard
    extra = 1
    ordering = ['order']

# ────────────────────────────────────────────────
# 📦 MemoDeck Admin
# ────────────────────────────────────────────────

@admin.register(MemoDeck)
class MemoDeckAdmin(admin.ModelAdmin):
    list_display = ('title', 'author', 'created_at', 'tag_list')
    search_fields = ('title', 'description', 'author__username', 'tags__name')
    list_filter = ('created_at', 'tags')
    ordering = ('-created_at',)
    autocomplete_fields = ('author', 'tags')
    readonly_fields = ('created_at',)
    inlines = [MemoCardInline]
    actions = ['export_to_json']

    def tag_list(self, obj):
        return ", ".join(tag.name for tag in obj.tags.all())
    tag_list.short_description = "Tags"

    @admin.action(description="Export selected decks to JSON")
    def export_to_json(self, request, queryset):

        def export_one(deck):
            # Just return the deck; BatchAction handles errors
            return deck

        result = BatchAction(queryset).run(export_one)

        # Build JSON output
        if queryset.count() == 1:
            deck = queryset.first()
            filename = f"{slugify(deck.title)}.json"
            content = json.dumps(deck.to_json(), indent=2)

        else:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
            filename = f"decks_{timestamp}.json"
            content = json.dumps(
                [deck.to_json() for deck in queryset],
                indent=2
            )

        # Send file to browser
        response = HttpResponse(content, content_type="application/json")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'

        BatchAction.display_messages(result, self.message_user, request, verb="export to JSON")
        return response



# ────────────────────────────────────────────────
# 🗂️ MemoCard Admin
# ────────────────────────────────────────────────

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
