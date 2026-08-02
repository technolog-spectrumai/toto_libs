from django.urls import path

from . import views

app_name = "clearing"

urlpatterns = [
    path("api/handshake/", views.handshake, name="handshake"),
    path("api/inbox/", views.inbox, name="inbox"),
]
