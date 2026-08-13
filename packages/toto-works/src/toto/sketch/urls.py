from django.urls import path

from .views import (
    SketchCreateView,
    SketchEditView,
    SketchIndexView,
    sketch_delete,
    sketch_save,
    sketch_source,
)

app_name = "sketch"

urlpatterns = [
    path("", SketchIndexView.as_view(), name="index"),
    path("new/", SketchCreateView.as_view(), name="create"),
    path("edit/<int:file_pk>/", SketchEditView.as_view(), name="edit"),
    path("save/<int:file_pk>/", sketch_save, name="save"),
    path("source/<int:file_pk>/", sketch_source, name="source"),
    path("delete/<int:file_pk>/", sketch_delete, name="delete"),
]
