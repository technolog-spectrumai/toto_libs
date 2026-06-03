from django.urls import path

from . import views
from .api_views import (
    BourseCenterApiView, BourseCreateApiView,
    BourseAcceptApiView, BourseRejectApiView, BourseCancelApiView,
)

app_name = "bourse"

urlpatterns = [
    path("", views.exchange_center, name="exchange_center"),
    path("proposals/new/", views.exchange_request_create, name="exchange_request_create"),
    path("proposals/<int:pk>/accept/", views.exchange_request_accept, name="exchange_request_accept"),
    path("proposals/<int:pk>/reject/", views.exchange_request_reject, name="exchange_request_reject"),
    path("proposals/<int:pk>/cancel/", views.exchange_request_cancel, name="exchange_request_cancel"),
    # Enigma JSON API
    path("api/enigma/center/", BourseCenterApiView.as_view(), name="api_enigma_center"),
    path("api/enigma/proposals/create/", BourseCreateApiView.as_view(), name="api_enigma_create"),
    path("api/enigma/proposals/<int:pk>/accept/", BourseAcceptApiView.as_view(), name="api_enigma_accept"),
    path("api/enigma/proposals/<int:pk>/reject/", BourseRejectApiView.as_view(), name="api_enigma_reject"),
    path("api/enigma/proposals/<int:pk>/cancel/", BourseCancelApiView.as_view(), name="api_enigma_cancel"),
]
