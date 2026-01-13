# finance/urls.py
from django.urls import path
from . import views

app_name = "assets"

urlpatterns = [
    path("assets/", views.asset_list, name="asset_list"),
    path("assets/<int:pk>/", views.asset_detail, name="asset_detail"),
]
