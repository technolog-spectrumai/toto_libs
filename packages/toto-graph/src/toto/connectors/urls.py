from django.urls import path

from . import views

app_name = "connectors"

urlpatterns = [
    path("", views.home, name="home"),
    path("new/", views.connector_edit, name="new"),
    path("<int:pk>/", views.connector_edit, name="detail"),
    path("<int:pk>/run/", views.run_now, name="run_now"),
    path("validate/", views.validate_config, name="validate_config"),
    path("runs/<int:pk>/", views.run_detail, name="run_detail"),
    path("runs/<int:pk>/review/", views.run_review, name="run_review"),
    path("runs/<int:pk>/status/", views.run_status, name="run_status"),
]
