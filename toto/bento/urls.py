from django.urls import path

from . import views
from .api_views import (
    BoxListApiView, BoxDetailApiView, CategoryListApiView,
    LinkListCreateApiView, LinkDeleteApiView, FullGraphApiView,
)

app_name = "bento"

urlpatterns = [
    path("", views.box_list, name="box_list"),
    # Enigma JSON API
    path("api/boxes/", BoxListApiView.as_view(), name="api_box_list"),
    path("api/boxes/<int:pk>/", BoxDetailApiView.as_view(), name="api_box_detail"),
    path("api/boxes/<int:pk>/links/", LinkListCreateApiView.as_view(), name="api_box_links"),
    path("api/links/", LinkListCreateApiView.as_view(), name="api_link_list"),
    path("api/links/<int:pk>/", LinkDeleteApiView.as_view(), name="api_link_detail"),
    path("api/categories/", CategoryListApiView.as_view(), name="api_category_list"),
    path("api/graph/", FullGraphApiView.as_view(), name="api_full_graph"),
    # Legacy HTML-support API
    path("api/boxes-graph/", views.api_boxes, name="api_boxes"),
    path("api/boxes/<int:pk>/graph/", views.api_box_graph, name="api_box_graph"),
    path("categories/", views.category_list, name="category_list"),
    path("categories/new/", views.category_create, name="category_create"),
    path("categories/<int:pk>/edit/", views.category_update, name="category_update"),
    path("new/", views.box_create, name="box_create"),
    path("<int:pk>/", views.box_detail, name="box_detail"),
    path("<int:pk>/edit/", views.box_update, name="box_update"),
    path("<int:pk>/delete/", views.box_delete, name="box_delete"),
    path("<int:pk>/lock/", views.box_lock, name="box_lock"),
    path("<int:pk>/unlock/", views.box_unlock, name="box_unlock"),
    path("links/new/", views.link_create, name="link_create"),
    path("links/<int:pk>/delete/", views.link_delete, name="link_delete"),
]
