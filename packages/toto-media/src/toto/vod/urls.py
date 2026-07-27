from django.urls import path

from . import views

app_name = "vod"

urlpatterns = [
    path("play/<int:file_pk>/", views.vault_file_play, name="vault_file_play"),
]
