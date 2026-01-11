import datetime
import io
import json
import zipfile
from django.contrib import admin
from django.http import HttpResponse
from django.template.defaultfilters import slugify

from .models import (
    MemoDeck,
    MemoCard,
    Tag,
    MermaidChart,
    MemoFormula,
)
from vault.models import VaultFile
from toto.batch import BatchAction


# ────────────────────────────────────────────────
# 🔖 Tag Admin
# ────────────────────────────────────────────────

@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)
    ordering = ("name",)


# ────────────────────────────────────────────────
# 📊 MermaidChart Admin
# ────────────────────────────────────────────────

@admin.register(MermaidChart)
class MermaidChartAdmin(admin.ModelAdmin):
    list_display = ("title",)
    search_fields = ("title", "description", "code")
    ordering = ("title",)


# ────────────────────────────────────────────────
# 🧮 MemoFormula Admin
# ────────────────────────────────────────────────

@admin.register(MemoFormula)
class MemoFormulaAdmin(admin.ModelAdmin):
    list_display = ("title",)
    search_fields = ("title", "latex", "description")
    ordering = ("title",)


# ────────────────────────────────────────────────
# 🧩 MemoCard Inline
# ────────────────────────────────────────────────

class MemoCardInline(admin.TabularInline):
    model = MemoCard
    extra = 1
    ordering = ["order"]
    fields = ("title", "content", "order", "image", "chart", "formula")
    autocomplete_fields = ("chart", "formula")


# ────────────────────────────────────────────────
# 📦 MemoDeck Admin
# ────────────────────────────────────────────────

@admin.register(MemoDeck)
class MemoDeckAdmin(admin.ModelAdmin):
    list_display = ("title", "author", "created_at", "tag_list")
    search_fields = ("title", "description", "author__username", "tags__name")
    list_filter = ("created_at", "tags")
    ordering = ("-created_at",)
    autocomplete_fields = ("author", "tags")
    readonly_fields = ("created_at",)
    inlines = [MemoCardInline]
    actions = ["export_to_zip"]

    def tag_list(self, obj):
        return ", ".join(tag.name for tag in obj.tags.all())
    tag_list.short_description = "Tags"

    @admin.action(description="Export selected decks as ZIP (JSON + images)")
    def export_to_zip(self, request, queryset):

        buffer = io.BytesIO()
        zip_file = zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED)

        for deck in queryset:
            deck_folder = slugify(deck.title)

            # 1) Add deck.json
            deck_json = json.dumps(deck.to_json(), indent=2)
            zip_file.writestr(f"{deck_folder}/deck.json", deck_json)

            # 2) Add card images
            for card in deck.cards.all():
                if card.image:
                    image_path = card.image.path
                    image_name = image_path.split("/")[-1]
                    zip_file.write(
                        image_path,
                        arcname=f"{deck_folder}/images/{image_name}"
                    )

        zip_file.close()

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        filename = f"decks_{timestamp}.zip"

        response = HttpResponse(buffer.getvalue(), content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


# ────────────────────────────────────────────────
# 🗂️ MemoCard Admin
# ────────────────────────────────────────────────

@admin.register(MemoCard)
class MemoCardAdmin(admin.ModelAdmin):
    list_display = ("title", "deck", "deck_author", "order")
    list_editable = ("order",)
    search_fields = ("title", "content", "deck__title", "deck__author__username")
    list_filter = ("deck__title",)
    autocomplete_fields = ("deck", "chart", "formula")

    def deck_author(self, obj):
        return obj.deck.author.username
    deck_author.short_description = "Deck Author"
