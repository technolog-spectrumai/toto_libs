import hashlib

from django.contrib import admin
from django.contrib import messages
from django.core.files.base import ContentFile
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html
from django import forms
from trix_editor.widgets import TrixEditorWidget

from toto.vault.models import VaultFile

from .forms import VerbenaPageLatexExportForm
from .latex import page_to_article_latex
from .models import Page, Section, Tag


# ────────────────────────────────────────────────
# REUSABLE BASE CLASSES (importable by other apps)
# ────────────────────────────────────────────────

def make_section_form(section_model):
    """Returns a ModelForm with TrixEditorWidget for the content field."""
    class _Form(forms.ModelForm):
        class Meta:
            model = section_model
            fields = "__all__"
            widgets = {"content": TrixEditorWidget()}
    return _Form


class SectionInlineMixin(admin.StackedInline):
    """
    Base inline for Section-like models. Subclasses must set `model`.
    The `form` is auto-built from `model` if not explicitly set.
    """
    extra = 1
    fields = ["title", "content", "author", "order"]
    ordering = ["order"]
    show_change_link = True

    def get_form_class(self):
        if not hasattr(self, "_auto_form"):
            self._auto_form = make_section_form(self.model)
        return self._auto_form

    def get_formset(self, request, obj=None, **kwargs):
        kwargs.setdefault("form", self.get_form_class())
        return super().get_formset(request, obj, **kwargs)


class PageAdminMixin(admin.ModelAdmin):
    """Base admin for Page-like models with slug auto-population."""
    prepopulated_fields = {"slug": ("title",)}
    search_fields = ["title", "description"]
    readonly_fields = ["created_at"]


# ────────────────────────────────────────────────
# VERBENA CONCRETE ADMIN
# ────────────────────────────────────────────────

class SectionInline(SectionInlineMixin):
    model = Section
    fields = ["title", "content", "author", "order"]


@admin.register(Page)
class PageAdmin(PageAdminMixin):
    list_display = ["title", "created_at", "author_list", "view_page"]
    list_filter = ["tags"]
    filter_horizontal = ["tags"]
    inlines = [SectionInline]
    actions = ["latex_export_console_action"]

    def get_urls(self):
        return [
            path(
                "latex-export-console/<int:page_id>/",
                self.admin_site.admin_view(self.latex_export_console_view),
                name="verbena_latex_export_console",
            ),
        ] + super().get_urls()

    def author_list(self, obj):
        authors = obj.authors()
        return ", ".join(a.full_name for a in authors) if authors else "—"
    author_list.short_description = "Authors"

    def view_page(self, obj):
        url = reverse("verbena:page_detail", kwargs={"slug": obj.slug})
        return format_html('<a href="{}" target="_blank">🔗 View</a>', url)
    view_page.short_description = "Page URL"

    def latex_export_console_action(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one Verbena page.", level=messages.ERROR)
            return
        return redirect(f"latex-export-console/{queryset.first().id}/")

    latex_export_console_action.short_description = "Export to LaTeX article in Vault"

    def latex_export_console_view(self, request, page_id):
        page = get_object_or_404(
            Page.objects.prefetch_related("sections"),
            id=page_id,
        )
        form = VerbenaPageLatexExportForm(
            user=request.user,
            initial={
                "file_name": f"{page.slug}.tex",
            },
        )

        if request.method == "POST":
            form = VerbenaPageLatexExportForm(request.POST, user=request.user)
            if form.is_valid():
                latex = page_to_article_latex(page)
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
                    notes=f"Exported from Verbena page '{page.title}' as a LaTeX article.",
                    content_hash=hashlib.sha256(content).hexdigest(),
                )
                vault_file.file.save(filename, ContentFile(content), save=False)
                vault_file.save()

                self.message_user(
                    request,
                    f"Exported '{page.title}' to Vault as {filename}.",
                    level=messages.SUCCESS,
                )
                return redirect(reverse("admin:verbena_page_change", args=[page.id]))

        context = {
            **self.admin_site.each_context(request),
            "title": f"LaTeX Export Console: {page.title}",
            "page": page,
            "form": form,
            "preview": page_to_article_latex(page),
            "back_url": reverse("admin:verbena_page_change", args=[page.id]),
        }
        return render(request, "admin/verbena_latex_export_console.html", context)


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ["title", "page", "order", "author"]
    list_filter = ["page", "author"]
    ordering = ["page", "order"]
    filter_horizontal = ["tags"]

    def get_form(self, request, obj=None, **kwargs):
        kwargs.setdefault("form", make_section_form(Section))
        return super().get_form(request, obj, **kwargs)


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ["name", "slug"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ("name",)}
