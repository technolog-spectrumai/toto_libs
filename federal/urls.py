# urls.py
from django.urls import path
from . import views
from django.views.generic import RedirectView

app_name = "federal"

urlpatterns = [
    path("api/federation/<slug:slug>/", views.federation_detail_json, name="federation_detail_json")
]
