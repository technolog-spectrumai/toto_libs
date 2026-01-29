from django.urls import path
from . import views

app_name = "chat"

urlpatterns = [
    path("rooms/", views.chat_view, name="chat_home"),
    path("rooms/<str:room_name>/", views.chat_view, name="chat_room"),
]
