from django.views.generic import ListView, DetailView
from django.shortcuts import get_object_or_404
from django.db.models import Q
from .models import Site, Article, Section, SubSection, Tag, Category

# 📰 List articles for a specific site, with optional filters
class FilteredArticleListView(ListView):
    model = Article
    template_name = 'kodama/article_list.html'
    context_object_name = 'articles'

    def get_queryset(self):
        site_slug = self.kwargs.get('site_slug')
        self.site = get_object_or_404(Site, slug=site_slug, active=True)

        queryset = Article.objects.filter(site=self.site)

        # 🔍 Text search
        query = self.request.GET.get('q')
        if query:
            queryset = queryset.filter(
                Q(title__icontains=query) |
                Q(summary__icontains=query)
            )

        # 🏷️ Tag filter
        tag_name = self.request.GET.get('tag')
        if tag_name:
            queryset = queryset.filter(tags__name__iexact=tag_name)

        # 📂 Category filter (if you add a category field later)
        category = self.request.GET.get('category')
        if category and hasattr(Article, 'category'):
            queryset = queryset.filter(category__iexact=category)

        return queryset.order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['site'] = self.site
        context['query'] = self.request.GET.get('q', '')
        context['tag'] = self.request.GET.get('tag', '')
        context['category'] = self.request.GET.get('category', '')
        context['categories'] = Category.objects.filter(site=self.site).order_by('name')
        context['tags'] = Tag.objects.filter(site=self.site).order_by('name')
        return context


class ArticleDetailView(DetailView):
    model = Article
    template_name = 'kodama/article_detail.html'
    context_object_name = 'article'
    slug_field = 'slug'
    slug_url_kwarg = 'article_slug'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['sections'] = self.object.sections.prefetch_related('subsections').order_by('order')
        context['site'] = self.object.site
        context['related_articles'] = (
            Article.objects.filter(site=self.object.site)
            .exclude(pk=self.object.pk)
            .order_by('-created_at')[:3]
        )
        return context

