from django.urls import path
from .views import VerbenaPageListByTagView, VerbenaPageDetailView

app_name = 'verbena'

urlpatterns = [
    path("tag/<slug:tag_slug>/", VerbenaPageListByTagView.as_view(), name="page_list_by_tag"),
    path("<slug:slug>/", VerbenaPageDetailView.as_view(), name="page_detail"),
]
