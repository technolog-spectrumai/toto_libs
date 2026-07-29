from django.urls import path

from . import views

app_name = "quota"

urlpatterns = [
    path("", views.index, name="index"),
    path("me/", views.my_usage, name="my_usage"),
    # Metric codes are dotted (texlab.compile), which <str:> matches happily —
    # it excludes "/" only.
    path("<str:code>/", views.metric_detail, name="metric_detail"),
    path("<str:code>/overrides/<int:pk>/delete/", views.override_delete, name="override_delete"),
]
