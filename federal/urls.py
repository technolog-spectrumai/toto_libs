# urls.py
from django.urls import path
from . import views

app_name = "federal"

urlpatterns = [
    path("api/federation/<slug:slug>/", views.federation_detail_json, name="federation_detail"),
    path("api/federation/<int:pk>/", views.federation_detail_json, name="federation_detail_json"),
    path("api/federations/", views.federation_list_json, name="federation_list_json"),
]
