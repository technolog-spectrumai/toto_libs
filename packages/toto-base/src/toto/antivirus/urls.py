from django.urls import path

from . import views

app_name = "antivirus"

urlpatterns = [
    path("", views.index, name="index"),
    path("statistics/", views.statistics, name="statistics"),
    path("pathology/", views.pathology, name="pathology"),
    path("preference/", views.set_preference, name="set_preference"),
    path("scan/<int:pk>/", views.scan_file, name="scan_file"),
]
