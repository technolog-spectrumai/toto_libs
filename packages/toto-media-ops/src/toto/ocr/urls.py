from django.urls import path

from . import views

app_name = "ocr"

urlpatterns = [
    path("", views.ocr_home, name="home"),
    path("run/", views.ocr_run, name="run"),
    path("ingest/", views.ocr_ingest, name="ingest"),
]
