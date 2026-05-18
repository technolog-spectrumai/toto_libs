from django.db import models
from django.views.generic import ListView, DetailView

from .models import Book, Article
from toto.palimpsest.models import Tag
from toto.ui import PageProcessor


class BookListView(ListView):
    model = Book
    template_name = "library/book_list.html"
    context_object_name = "items"
    paginate_by = 24

    def get_queryset(self):
        qs = Book.objects.prefetch_related("tags")

        q = self.request.GET.get("q")
        if q:
            qs = qs.filter(
                models.Q(title__icontains=q) |
                models.Q(abstract__icontains=q) |
                models.Q(authors__icontains=q) |
                models.Q(isbn__icontains=q)
            )

        tag_slug = self.request.GET.get("tag")
        if tag_slug:
            qs = qs.filter(tags__slug=tag_slug)
            self.active_tag = Tag.objects.filter(slug=tag_slug).first()
        else:
            self.active_tag = None

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tags"] = Tag.objects.all().order_by("name")
        context["active_tag"] = getattr(self, "active_tag", None)
        context["item_type"] = "book"
        return PageProcessor().decorate(context, self.request)


class BookDetailView(DetailView):
    model = Book
    template_name = "library/item_detail.html"
    context_object_name = "item"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["item_type"] = "book"
        context["back_url"] = "library:book_list"
        return PageProcessor().decorate(context, self.request)


class ArticleListView(ListView):
    model = Article
    template_name = "library/article_list.html"
    context_object_name = "items"
    paginate_by = 24

    def get_queryset(self):
        qs = Article.objects.prefetch_related("tags")

        q = self.request.GET.get("q")
        if q:
            qs = qs.filter(
                models.Q(title__icontains=q) |
                models.Q(abstract__icontains=q) |
                models.Q(authors__icontains=q) |
                models.Q(journal__icontains=q)
            )

        tag_slug = self.request.GET.get("tag")
        if tag_slug:
            qs = qs.filter(tags__slug=tag_slug)
            self.active_tag = Tag.objects.filter(slug=tag_slug).first()
        else:
            self.active_tag = None

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tags"] = Tag.objects.all().order_by("name")
        context["active_tag"] = getattr(self, "active_tag", None)
        context["item_type"] = "article"
        return PageProcessor().decorate(context, self.request)


class ArticleDetailView(DetailView):
    model = Article
    template_name = "library/item_detail.html"
    context_object_name = "item"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["item_type"] = "article"
        context["back_url"] = "library:article_list"
        return PageProcessor().decorate(context, self.request)
