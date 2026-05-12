from django.db import models
from django.views.generic import ListView, DetailView
from django.utils.safestring import mark_safe

from .models import Page, Tag
from toto.core.page import PageProcessor


# ────────────────────────────────────────────────
# LIST VIEW (all pages or filter by tag)
# ────────────────────────────────────────────────

class PageListView(ListView):
    """
    Displays all pages, optionally filtered by tag.
    Supports search by title or description.
    """
    model = Page
    template_name = "verbena/page_list.html"
    context_object_name = "pages"
    paginate_by = 20
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = super().get_queryset()

        # Search query
        query = self.request.GET.get("q")
        if query:
            qs = qs.filter(
                models.Q(title__icontains=query) |
                models.Q(description__icontains=query)
            )

        # Optional tag filter
        tag_slug = self.kwargs.get("tag_slug")
        if tag_slug:
            self.tag = Tag.objects.get(slug=tag_slug)
            qs = qs.filter(tags=self.tag)
        else:
            self.tag = None

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tags"] = Tag.objects.all().order_by("name")
        context["tag"] = getattr(self, "tag", None)
        return PageProcessor().decorate(context, self.request)


# ────────────────────────────────────────────────
# DETAIL VIEW
# ────────────────────────────────────────────────

class VerbenaPageDetailView(DetailView):
    """
    Displays a single page with sections and bibliographic references.
    Sections store rich Trix HTML content directly.
    """
    model = Page
    template_name = "verbena/page_detail.html"
    context_object_name = "page"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # ─────────── Render Sections ───────────
        rendered_sections = []
        for section in self.object.sections.all():
            rendered_sections.append({
                "title": section.title,
                "author": section.author,
                "html": mark_safe(section.content),  # Trix content is HTML
                "tags": section.tags.all(),
            })
        context["sections"] = rendered_sections

        # ─────────── Tag for "Back to Page List" ───────────
        context["tag_slug"] = (
            self.object.tags.first().slug
            if self.object.tags.exists()
            else None
        )

        # ─────────── Bibliographic References with Vault Links ───────────
        references = []
        for ref in self.object.references.all().order_by("-year", "title"):
            vault_file_url = ref.vault_file.get_public_url() if ref.vault_file else None
            references.append({
                "title": ref.title,
                "authors": [a.full_name for a in ref.authors.all()],
                "year": ref.year,
                "type": ref.bibtex_type,
                "vault_file_url": vault_file_url,
            })
        context["references"] = references

        return PageProcessor().decorate(context, self.request)