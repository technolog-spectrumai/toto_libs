from django.urls import path

from . import views

app_name = "backup"

urlpatterns = [
    path(
        "stored/<uuid:uid>/<str:magic_token>/stream/",
        views.stored_backup_stream,
        name="stored_backup_stream",
    ),
]
