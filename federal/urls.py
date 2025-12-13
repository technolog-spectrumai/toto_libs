# urls.py
from django.urls import path
from . import views
from django.views.generic import RedirectView

app_name = "federal"

urlpatterns = []
#     path("", RedirectView.as_view(pattern_name="federal:current_federation", permanent=False)),
#     path("api/federation/<slug:slug>/", views.federation_detail_json, name="federation_detail_json"),
#     path("federation/<slug:slug>/", views.federation_detail_json, name="federation_detail"),
#     path("api/federations/", views.federation_list_json, name="federation_list_json"),
#     path("current/", views.current_federation_view, name="current_federation"),
# ]
