from django.views.generic import ListView, DetailView
from django.utils.safestring import mark_safe
from markdownx.utils import markdownify
from .models import Page, Tag
from oya.page import PageProcessor


class VerbenaTagListView(ListView):
    model = Tag
    template_name = "verbena/tag_list.html"
    context_object_name = "tags"
    ordering = ["name"]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return PageProcessor().decorate(context, self.request)


class VerbenaPageListByTagView(ListView):
    model = Page
    template_name = "verbena/page_list.html"
    context_object_name = "pages"

    def get_queryset(self):
        tag_slug = self.kwargs["tag_slug"]
        self.tag = Tag.objects.get(slug=tag_slug)
        return Page.objects.filter(tags=self.tag).order_by("title")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tag"] = self.tag
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
                "image": section.image,  # updated: one image per section
                "html": mark_safe(markdownify(section.content)),
            })

        context["sections"] = rendered_sections

        # remember tag slug for "Back to Page List"
        context["tag_slug"] = (
            self.object.tags.first().slug
            if self.object.tags.exists()
            else None
        )

        return PageProcessor().decorate(context, self.request)
