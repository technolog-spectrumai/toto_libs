from django.urls import path

from . import views

app_name = "bourse"

urlpatterns = [
    path("", views.exchange_center, name="exchange_center"),
    path("proposals/new/", views.exchange_request_create, name="exchange_request_create"),
    path("proposals/<int:pk>/accept/", views.exchange_request_accept, name="exchange_request_accept"),
    path("proposals/<int:pk>/reject/", views.exchange_request_reject, name="exchange_request_reject"),
    path("proposals/<int:pk>/cancel/", views.exchange_request_cancel, name="exchange_request_cancel"),
]
