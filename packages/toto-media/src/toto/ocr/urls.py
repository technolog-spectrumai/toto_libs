from django.urls import path

from . import views

app_name = "ocr"

#: `submit` and `builder` are the two paths nginx gives a raised body limit to
#: (deploy.py::_nginx_ocr_upload_location). Renaming either without changing
#: that regex would silently restore the 10 MB server-wide cap.
urlpatterns = [
    path("", views.ocr_home, name="home"),
    path("builder/", views.ocr_home, name="builder"),
    path("submit/", views.ocr_submit, name="submit"),
    path("run/<int:pk>/", views.ocr_run_detail, name="run"),
    path("run/<int:pk>/status/", views.ocr_status, name="status"),
    path("run/<int:pk>/text/", views.ocr_text, name="text"),
    path("run/<int:pk>/save/", views.ocr_save, name="save"),
    path("run/<int:pk>/cancel/", views.ocr_cancel, name="cancel"),
    path("run/<int:pk>/retry/", views.ocr_retry, name="retry"),
]
