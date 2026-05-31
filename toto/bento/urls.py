from django.urls import path

from . import views

app_name = "bento"

urlpatterns = [
    path("", views.box_list, name="box_list"),
    path("api/boxes/", views.api_boxes, name="api_boxes"),
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
