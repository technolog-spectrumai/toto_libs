from django.urls import path

from . import views

app_name = "antivirus"

urlpatterns = [
    path("", views.index, name="index"),
    path("preference/", views.set_preference, name="set_preference"),
    path("scan/<int:pk>/", views.scan_file, name="scan_file"),
]
