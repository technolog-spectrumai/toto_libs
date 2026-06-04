from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import models
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import DetailView, ListView

from toto.ui import PageProcessor
from toto.verbena.views import PageDetailMixin

from .forms import PageForm, SectionForm, TagForm
from .models import Page, Tag


class PalimpsestListView(ListView):
    model = Page
    template_name = "palimpsest/page_list.html"
    context_object_name = "pages"
    paginate_by = 20

    def get_queryset(self):
        qs = (
            Page.objects
            .prefetch_related("tags", "sections", "sections__author")
            .order_by("-created_at")
        )
        if not self.request.user.is_authenticated:
            qs = qs.filter(is_private=False)

        query = self.request.GET.get("q")
        if query:
            qs = qs.filter(
                models.Q(title__icontains=query) |
                models.Q(description__icontains=query) |
                models.Q(sections__title__icontains=query) |
                models.Q(sections__content__icontains=query)
            ).distinct()

        tag_slug = self.kwargs.get("tag_slug")
        if tag_slug:
            self.tag = Tag.objects.get(slug=tag_slug)
            qs = qs.filter(tags=self.tag)
        else:
            self.tag = None
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["featured_page"] = context["pages"][0] if context["pages"] else None
        context["tags"] = Tag.objects.all().order_by("name")
        context["tag"] = getattr(self, "tag", None)
        context["publication_name"] = "Blog"
        return PageProcessor().decorate(context, self.request)


class PalimpsestDetailView(PageDetailMixin, DetailView):
    model = Page
    template_name = "palimpsest/page_detail.html"
    context_object_name = "page"
    slug_field = "slug"
    slug_url_kwarg = "slug"
    queryset = Page.objects.prefetch_related("tags", "sections", "sections__author")

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        if obj.is_private and not self.request.user.is_authenticated:
            raise Http404
        return obj

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["sections"] = self.render_sections(self.object)
        context["back_url"] = "palimpsest:page_list"
        context["back_label"] = "Blog"
        context["page_type_label"] = "Essay"
        context["publication_name"] = "Blog"
        context["tag_slug"] = (
            self.object.tags.first().slug if self.object.tags.exists() else None
        )
        return PageProcessor().decorate(context, self.request)


def palimpsest_render(request, template_name, context):
    context.setdefault("publication_name", "Blog")
    return render(request, template_name, PageProcessor().decorate(context, request))


@login_required
def page_create(request):
    if request.method == "POST":
        form = PageForm(request.POST)
        if form.is_valid():
            page = form.save()
            messages.success(request, "Piece created. Add a first section when you are ready.")
            return redirect("palimpsest:page_detail", slug=page.slug)
    else:
        form = PageForm()

    return palimpsest_render(request, "palimpsest/page_form.html", {
        "form": form,
        "title": "New piece",
        "submit_label": "Create piece",
        "icon": "fa-solid fa-plus",
    })


@login_required
def page_update(request, slug):
    page = get_object_or_404(Page, slug=slug)

    if request.method == "POST":
        form = PageForm(request.POST, instance=page)
        if form.is_valid():
            page = form.save()
            messages.success(request, "Piece details saved.")
            return redirect("palimpsest:page_detail", slug=page.slug)
    else:
        form = PageForm(instance=page)

    return palimpsest_render(request, "palimpsest/page_form.html", {
        "form": form,
        "page": page,
        "title": "Edit piece",
        "submit_label": "Save piece",
        "icon": "fa-solid fa-pen-to-square",
    })


@login_required
def page_delete(request, slug):
    page = get_object_or_404(Page, slug=slug)

    if request.method == "POST":
        page.delete()
        messages.success(request, "Piece deleted.")
        return redirect("palimpsest:page_list")

    return palimpsest_render(request, "palimpsest/page_confirm_delete.html", {
        "page": page,
    })


@login_required
def section_create(request, slug):
    page = get_object_or_404(Page, slug=slug)
    initial = {"order": page.sections.count() + 1}

    if request.method == "POST":
        form = SectionForm(request.POST)
        if form.is_valid():
            section = form.save(commit=False)
            section.page = page
            section.save()
            form.save_m2m()
            messages.success(request, "Section added.")
            return redirect("palimpsest:page_detail", slug=page.slug)
    else:
        form = SectionForm(initial=initial)

    return palimpsest_render(request, "palimpsest/section_form.html", {
        "form": form,
        "page": page,
        "title": "Add section",
        "submit_label": "Add section",
        "icon": "fa-solid fa-file-lines",
    })


@login_required
def section_update(request, slug, pk):
    page = get_object_or_404(Page, slug=slug)
    section = get_object_or_404(page.sections, pk=pk)

    if request.method == "POST":
        form = SectionForm(request.POST, instance=section)
        if form.is_valid():
            form.save()
            messages.success(request, "Section saved.")
            return redirect("palimpsest:page_detail", slug=page.slug)
    else:
        form = SectionForm(instance=section)

    return palimpsest_render(request, "palimpsest/section_form.html", {
        "form": form,
        "page": page,
        "section": section,
        "title": "Edit section",
        "submit_label": "Save section",
        "icon": "fa-solid fa-pen-to-square",
    })


@login_required
def section_delete(request, slug, pk):
    page = get_object_or_404(Page, slug=slug)
    section = get_object_or_404(page.sections, pk=pk)

    if request.method == "POST":
        section.delete()
        messages.success(request, "Section deleted.")
        return redirect("palimpsest:page_detail", slug=page.slug)

    return palimpsest_render(request, "palimpsest/section_confirm_delete.html", {
        "page": page,
        "section": section,
    })


@login_required
def tag_create(request):
    if request.method == "POST":
        form = TagForm(request.POST)
        if form.is_valid():
            tag = form.save()
            messages.success(request, "Tag created.")
            return redirect("palimpsest:page_list_by_tag", tag_slug=tag.slug)
    else:
        form = TagForm()

    return palimpsest_render(request, "palimpsest/tag_form.html", {
        "form": form,
        "title": "New tag",
        "submit_label": "Create tag",
        "icon": "fa-solid fa-tag",
    })
