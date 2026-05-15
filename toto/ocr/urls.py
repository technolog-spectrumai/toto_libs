from django.urls import path
from . import views

app_name = "ocr"

urlpatterns = [
    path("", views.OcrProjectListView.as_view(), name="project_list"),
    path("<slug:slug>/", views.OcrProjectDetailView.as_view(), name="project_detail"),

    path("image/<int:image_id>/", views.OcrImageDetailView.as_view(), name="image_detail"),
    path("image/<int:image_id>/run/", views.ocr_run, name="ocr_run"),
    path("<slug:slug>/upload/", views.ocr_image_upload, name="image_upload"),
    path("image/<int:image_id>/delete/", views.ocr_image_delete, name="image_delete"),
    path("image/<int:image_id>/transform/", views.apply_transform, name="apply_transform"),
    path("image/<int:image_id>/duplicate/", views.image_duplicate, name="image_duplicate"),
    path("image/<int:image_id>/promote-to-bento/", views.promote_image_text_to_bento, name="promote_to_bento"),
]
