from django.urls import path
from . import views

app_name = "assets"

urlpatterns = [
    path("", views.assets_list, name="assets_list"),  # /assets/
    path("<int:pk>/", views.asset_detail, name="asset_detail"),  # /assets/5/
]
