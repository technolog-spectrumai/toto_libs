from django.urls import path

from . import views

app_name = "ocr"

urlpatterns = [
    path("vault/<int:file_pk>/", views.run_page, name="run_page"),
]
