from django.urls import path
from .views import PageListView, VerbenaPageDetailView

app_name = 'verbena'

urlpatterns = [
    # All pages or filtered by tag
    path("", PageListView.as_view(), name="page_list"),
    path("tag/<slug:tag_slug>/", PageListView.as_view(), name="page_list_by_tag"),

    # Page detail
    path("<slug:slug>/", VerbenaPageDetailView.as_view(), name="page_detail"),
]