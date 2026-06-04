from django.urls import path

from . import views

app_name = "leasing"

urlpatterns = [
    path("", views.lease_list, name="lease_list"),
    path("new/", views.lease_create, name="lease_create"),
    path("<int:pk>/", views.lease_detail, name="lease_detail"),
    path("<int:pk>/activate/", views.lease_activate, name="lease_activate"),
    path("<int:pk>/cancel/", views.lease_cancel, name="lease_cancel"),
]
