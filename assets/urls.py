from django.urls import path
from . import views

app_name = "assets"

urlpatterns = [
    path("", views.assets_list, name="assets_list"),  # /assets/
    #path("<int:pk>/", views.public_asset_detail, name="public_asset_detail"),  # /assets/5/
]
