from django.db import models
from django.views.generic import ListView, DetailView
from django.utils.safestring import mark_safe
from markdownx.utils import markdownify

from .models import Page, Tag
from toto.core.page import PageProcessor


# ────────────────────────────────────────────────
# LIST VIEW (all articles or filter by tag)
# ────────────────────────────────────────────────

class ArticleListView(ListView):
    """
    Displays all articles, optionally filtered by tag.
    Supports search by title or description.
    """
    model = Page
    template_name = "verbena/article_list.html"
    context_object_name = "articles"
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
    Displays a single article page with sections and subsections.
    """
    model = Page
    template_name = "verbena/article_detail.html"
    context_object_name = "page"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        rendered_sections = []
        for section in self.object.sections.all():
            rendered_subsections = []
            for sub in section.subsections.all():
                rendered_subsections.append({
                    "title": sub.title,
                    "image": sub.image,
                    "html": mark_safe(markdownify(sub.content))
                })

            rendered_sections.append({
                "title": section.title,
                "author": section.author,
                "html": mark_safe(markdownify(section.content)),
                "tags": section.tags.all(),
                "subsections": rendered_subsections,
            })

        context["sections"] = rendered_sections

        # For "Back to Page List"
        context["tag_slug"] = (
            self.object.tags.first().slug
            if self.object.tags.exists()
            else None
        )

        return PageProcessor().decorate(context, self.request)