from django.urls import path

from . import views

app_name = "ocr"

urlpatterns = [
    path("", views.ocr_home, name="home"),
    path("result/<int:run_id>/", views.ocr_result, name="result"),
    path("result/<int:run_id>/status/", views.ocr_status, name="status"),
    path("result/<int:run_id>/export-bento/", views.ocr_export_bento, name="export_bento"),
]
