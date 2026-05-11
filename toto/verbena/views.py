from django.db import models
from django.views.generic import ListView, DetailView
from django.utils.safestring import mark_safe
from markdownx.utils import markdownify

from .models import Page, Tag
from toto.core.page import PageProcessor


# ────────────────────────────────────────────────
# LIST VIEWS
# ────────────────────────────────────────────────

class ArticleListView(ListView):
    model = Page
    template_name = "verbena/article_list.html"
    context_object_name = "articles"
    paginate_by = 20
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = super().get_queryset()

        query = self.request.GET.get("q")
        if query:
            qs = qs.filter(
                models.Q(title__icontains=query) |
                models.Q(description__icontains=query)
            )

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tags"] = Tag.objects.all().order_by("name")
        return PageProcessor().decorate(context, self.request)


class VerbenaPageListByTagView(ListView):
    model = Page
    template_name = "verbena/article_list_by_tag.html"
    context_object_name = "pages"

    def get_queryset(self):
        tag_slug = self.kwargs["tag_slug"]
        self.tag = Tag.objects.get(slug=tag_slug)
        return Page.objects.filter(tags=self.tag).order_by("title")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tag"] = self.tag
        return PageProcessor().decorate(context, self.request)


# ────────────────────────────────────────────────
# DETAIL VIEW
# ────────────────────────────────────────────────

class VerbenaPageDetailView(DetailView):
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
                    "html": mark_safe(markdownify(sub.content)),
                    "topics": [t.name for t in sub.topics.all()],
                })

            rendered_sections.append({
                "title": section.title,
                "author": section.author,
                "html": mark_safe(markdownify(section.content)),
                "tags": section.tags.all(),
                "topics": [t.name for t in section.topics.all()],
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