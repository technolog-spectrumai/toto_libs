from django.db import models
from django.views.generic import ListView, DetailView
from django.utils.safestring import mark_safe

from .models import Page, Tag
from toto.core.page import PageProcessor


class PageListView(ListView):
    model = Page
    template_name = "verbena/page_list.html"
    context_object_name = "pages"
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


class VerbenaPageDetailView(DetailView):
    model = Page
    template_name = "verbena/page_detail.html"
    context_object_name = "page"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        rendered_sections = []
        for section in self.object.sections.all():
            rendered_sections.append({
                "title": section.title,
                "author": section.author,
                "html": mark_safe(section.content),
                "tags": section.tags.all(),
            })
        context["sections"] = rendered_sections

        context["tag_slug"] = (
            self.object.tags.first().slug
            if self.object.tags.exists()
            else None
        )

        return PageProcessor().decorate(context, self.request)
