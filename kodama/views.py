from django.views.generic import ListView, DetailView
from django.shortcuts import get_object_or_404
from django.db.models import Q
from .models import Site, Article, Section, SubSection, Tag, Category, Menu


class KodamaSiteMixin:
    site_slug_kwarg = 'site_slug'

    def get_site(self):
        slug = self.kwargs.get(self.site_slug_kwarg)
        return get_object_or_404(Site, slug=slug, active=True)

    def get_menus(self, site):
        return Menu.objects.filter(site=site).prefetch_related('category_links__category').order_by('order')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        site = getattr(self, 'site', None) or self.get_site()
        context['site'] = site
        context['menus'] = self.get_menus(site)
        context['categories'] = Category.objects.filter(site=site).order_by('name')
        context['tags'] = Tag.objects.filter(site=site).order_by('name')
        return context


# List articles for a specific site, with optional filters
class FilteredArticleListView(KodamaSiteMixin, ListView):
    model = Article
    template_name = 'kodama/article_list.html'
    context_object_name = 'articles'

    def get_queryset(self):
        self.site = self.get_site()
        queryset = Article.objects.filter(site=self.site)

        query = self.request.GET.get('q')
        if query:
            queryset = queryset.filter(Q(title__icontains=query) | Q(summary__icontains=query))

        tag_name = self.request.GET.get('tag')
        if tag_name:
            queryset = queryset.filter(tags__name__iexact=tag_name)

        category = self.request.GET.get('category')
        if category:
            queryset = queryset.filter(category__slug=category)

        return queryset.order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['query'] = self.request.GET.get('q', '')
        context['tag'] = self.request.GET.get('tag', '')
        context['category'] = self.request.GET.get('category', '')
        return context

# Article detail view with sections and related articles
class ArticleDetailView(KodamaSiteMixin, DetailView):
    model = Article
    template_name = 'kodama/article_detail.html'
    context_object_name = 'article'
    slug_field = 'slug'
    slug_url_kwarg = 'article_slug'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['sections'] = self.object.sections.prefetch_related('subsections').order_by('order')
        context['related_articles'] = (
            Article.objects.filter(site=self.object.site)
            .exclude(pk=self.object.pk)
            .order_by('-created_at')[:3]
        )
        return context
