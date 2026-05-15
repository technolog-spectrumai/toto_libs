import datetime
import hashlib
import io
import json
import zipfile
from django.contrib import admin, messages
from django.core.files.base import ContentFile
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.defaultfilters import slugify
from django.urls import path, reverse
from django.utils.html import format_html

from toto.memo.forms import MemoDeckLatexExportForm
from toto.memo.latex import clean_theme_name, deck_to_beamer_latex
from .models import (
    MemoDeck,
    MemoCard,
    Tag,
    MemoDiagram,
)
from toto.core.batch import BatchAction
from toto.vault.models import VaultFile


# ────────────────────────────────────────────────
# 🔖 Tag Admin
# ────────────────────────────────────────────────

@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)
    ordering = ("name",)


# ────────────────────────────────────────────────
# 📊 MemoDiagram Admin
# ────────────────────────────────────────────────

@admin.register(MemoDiagram)
class MemoDiagramAdmin(admin.ModelAdmin):
    list_display = ("title", "svg_file", "svg_file_type", "svg_url")
    search_fields = ("title", "description", "svg_file__title", "svg_file__key")
    autocomplete_fields = ("svg_file",)
    ordering = ("title",)
    readonly_fields = ("svg_url",)

    def svg_file_type(self, obj):
        return obj.svg_file.file_type
    svg_file_type.short_description = "File type"

    def svg_url(self, obj):
        if not obj.svg_url:
            return "—"
        return format_html('<a href="{}" target="_blank">Open SVG</a>', obj.svg_url)
    svg_url.short_description = "SVG URL"


# ────────────────────────────────────────────────
# 🧩 MemoCard Inline
# ────────────────────────────────────────────────

class MemoCardInline(admin.TabularInline):
    model = MemoCard
    extra = 1
    ordering = ["order"]
    fields = ("title", "content", "order", "image", "diagram")
    autocomplete_fields = ("diagram",)


# ────────────────────────────────────────────────
# 📦 MemoDeck Admin
# ────────────────────────────────────────────────

@admin.register(MemoDeck)
class MemoDeckAdmin(admin.ModelAdmin):
    list_display = ("slug", "title", "author", "created_at", "tag_list")
    search_fields = ("title", "description", "author__username", "tags__name")
    list_filter = ("created_at", "tags")
    ordering = ("-created_at",)
    autocomplete_fields = ("author", "tags")
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("created_at",)
    inlines = [MemoCardInline]
    actions = ["export_to_zip", "latex_export_console_action"]

    def get_urls(self):
        return [
            path(
                "latex-export-console/<int:deck_id>/",
                self.admin_site.admin_view(self.latex_export_console_view),
                name="memo_latex_export_console",
            ),
        ] + super().get_urls()

    def tag_list(self, obj):
        return ", ".join(tag.name for tag in obj.tags.all())
    tag_list.short_description = "Tags"

    def latex_export_console_action(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one memo deck.", level=messages.ERROR)
            return
        return redirect(f"latex-export-console/{queryset.first().id}/")

    latex_export_console_action.short_description = "Export to Beamer LaTeX in Vault"

    def latex_export_console_view(self, request, deck_id):
        deck = get_object_or_404(
            MemoDeck.objects.prefetch_related("cards", "cards__diagram"),
            id=deck_id,
        )
        form = MemoDeckLatexExportForm(
            user=request.user,
            initial={
                "theme": "Madrid",
                "file_name": f"{deck.slug}-beamer.tex",
            },
        )

        if request.method == "POST":
            form = MemoDeckLatexExportForm(request.POST, user=request.user)
            if form.is_valid():
                theme = clean_theme_name(form.cleaned_data["theme"])
                latex = deck_to_beamer_latex(deck, theme=theme)
                key = form.cleaned_data["file_name"]
                filename = f"{key}.tex"
                content = latex.encode("utf-8")

                vault_file = VaultFile(
                    owner=request.user,
                    title=filename,
                    key=key,
                    file_type="text",
                    bucket=form.cleaned_data["bucket"],
                    is_public=form.cleaned_data["make_public"],
                    notes=f"Exported from memo deck '{deck.title}' with Beamer theme {theme}.",
                    content_hash=hashlib.sha256(content).hexdigest(),
                )
                vault_file.file.save(filename, ContentFile(content), save=False)
                vault_file.save()

                self.message_user(
                    request,
                    f"Exported '{deck.title}' to Vault as {filename}.",
                    level=messages.SUCCESS,
                )
                return redirect(reverse("admin:memo_memodeck_change", args=[deck.id]))

        context = {
            **self.admin_site.each_context(request),
            "title": f"LaTeX Export Console: {deck.title}",
            "deck": deck,
            "form": form,
            "preview": deck_to_beamer_latex(deck, theme=form.initial.get("theme", "Madrid")),
            "back_url": reverse("admin:memo_memodeck_change", args=[deck.id]),
        }
        return render(request, "admin/memo_latex_export_console.html", context)

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
    search_fields = ("title", "content", "deck__title", "deck__author__username", "diagram__title")
    list_filter = ("deck__title",)
    autocomplete_fields = ("deck", "diagram")

    def deck_author(self, obj):
        return obj.deck.author.username
    deck_author.short_description = "Deck Author"
