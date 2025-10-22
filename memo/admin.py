from django.contrib import admin
from adminsortable2.admin import SortableAdminMixin
from .models import MemoDeck, MemoCard, Tag
from .export import DeckLatexExporter
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
    actions = ['export_to_latex']

    def tag_list(self, obj):
        return ", ".join(tag.name for tag in obj.tags.all())
    tag_list.short_description = "Tags"

    @admin.action(description="Export selected decks to LaTeX source files")
    def export_to_latex(self, request, queryset):

        def export_one(deck):
            exporter = DeckLatexExporter(deck)
            tex_file = exporter.export_to_latex()

            if not tex_file or not tex_file.file:
                raise FileNotFoundError("LaTeX file was not generated.")

            return deck

        result = BatchAction(queryset).run(export_one)
        BatchAction.display_messages(result, self.message_user, request, verb="export to LaTeX")



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
