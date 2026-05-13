from django.urls import path
from .views import BookListView, BookDetailView, ArticleListView, ArticleDetailView

app_name = "library"

urlpatterns = [
    path("books/", BookListView.as_view(), name="book_list"),
    path("books/<slug:slug>/", BookDetailView.as_view(), name="book_detail"),
    path("articles/", ArticleListView.as_view(), name="article_list"),
    path("articles/<slug:slug>/", ArticleDetailView.as_view(), name="article_detail"),
]
