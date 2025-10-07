from django.contrib import admin
from django.utils.text import slugify
from django.core.files import File
from adminsortable2.admin import SortableAdminMixin
from .models import MemoDeck, MemoCard, Tag
from .pdf import MemoDeckCompiler
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
    actions = ['compile_pdf_and_send_to_vault']

    def tag_list(self, obj):
        return ", ".join(tag.name for tag in obj.tags.all())
    tag_list.short_description = "Tags"

    @admin.action(description="Compile selected decks to PDF and send to Vault")
    def compile_pdf_and_send_to_vault(self, request, queryset):

        def compile_one(deck):
            compiler = MemoDeckCompiler(deck)
            pdf_path = compiler.generate_pdf()

            if not pdf_path:
                raise FileNotFoundError("PDF file not found after generation.")

            with open(pdf_path, 'rb') as f:
                VaultFile.objects.create(
                    owner=deck.author,
                    title=deck.title,
                    file=File(f, name=f"{slugify(deck.title)}.pdf"),
                    file_type='pdf',
                    notes=f"Compiled from MemoDeck ID {deck.id}"
                )

            return deck

        result = BatchAction(queryset).run(compile_one)
        BatchAction.display_messages(result, self.message_user, request, verb="compile and vault")

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
