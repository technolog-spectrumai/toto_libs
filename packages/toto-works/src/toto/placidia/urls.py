from django.urls import path

from .views import BountyBoardView

app_name = "placidia"

urlpatterns = [
    path("", BountyBoardView.as_view(), name="board"),
]
