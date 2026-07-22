from django.urls import path

from . import views

app_name = "texplay"

urlpatterns = [
    path("file/<int:file_pk>/", views.latex_play, name="latex_play"),
    path("file/<int:file_pk>/compile/", views.latex_compile, name="latex_compile"),
    path("job/<int:job_pk>/status/", views.latex_job_status, name="latex_job_status"),
]
