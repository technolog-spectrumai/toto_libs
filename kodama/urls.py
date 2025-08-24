from django.urls import path
from .views import FilteredArticleListView, ArticleDetailView

urlpatterns = [
    path('site/<slug:site_slug>/articles/', FilteredArticleListView.as_view(), name='article-list'),
    path('site/<slug:site_slug>/article/<slug:article_slug>/', ArticleDetailView.as_view(), name='article-detail'),
]
