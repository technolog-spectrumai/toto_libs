from django.urls import path

from . import views

app_name = "transcription"

urlpatterns = [
    path("", views.home, name="home"),
    path("vault/<int:file_pk>/", views.run_page, name="run_page"),
]
