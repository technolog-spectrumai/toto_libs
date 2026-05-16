from django.urls import path

from . import views
from .views import PalimpsestDetailView, PalimpsestListView


app_name = "palimpsest"

urlpatterns = [
    path("", PalimpsestListView.as_view(), name="page_list"),
    path("new/", views.page_create, name="page_create"),
    path("tags/new/", views.tag_create, name="tag_create"),
    path("tag/<slug:tag_slug>/", PalimpsestListView.as_view(), name="page_list_by_tag"),
    path("<slug:slug>/edit/", views.page_update, name="page_update"),
    path("<slug:slug>/delete/", views.page_delete, name="page_delete"),
    path("<slug:slug>/sections/new/", views.section_create, name="section_create"),
    path("<slug:slug>/sections/<int:pk>/edit/", views.section_update, name="section_update"),
    path("<slug:slug>/sections/<int:pk>/delete/", views.section_delete, name="section_delete"),
    path("<slug:slug>/layers/new/", views.section_create, name="section_create_legacy"),
    path("<slug:slug>/layers/<int:pk>/edit/", views.section_update, name="section_update_legacy"),
    path("<slug:slug>/layers/<int:pk>/delete/", views.section_delete, name="section_delete_legacy"),
    path("<slug:slug>/", PalimpsestDetailView.as_view(), name="page_detail"),
]
