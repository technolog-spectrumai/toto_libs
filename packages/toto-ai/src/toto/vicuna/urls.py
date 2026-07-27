from django.urls import path

from . import views

app_name = "vicuna"

urlpatterns = [
    path("chat/", views.chat, name="chat"),
]
