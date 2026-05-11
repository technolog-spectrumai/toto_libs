from django.urls import path
from .views import ArticleListView, VerbenaPageDetailView

app_name = 'verbena'

urlpatterns = [
    # All articles or filtered by tag
    path("", ArticleListView.as_view(), name="article_list"),
    path("tag/<slug:tag_slug>/", ArticleListView.as_view(), name="page_list_by_tag"),

    # Article detail
    path("<slug:slug>/", VerbenaPageDetailView.as_view(), name="page_detail"),
]