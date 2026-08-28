from django.urls import path
from django.views.generic import RedirectView

from .views import (
    SketchCreateView,
    SketchEditView,
    sketch_delete,
    sketch_save,
    sketch_source,
)

app_name = "sketch"

#: RedirectView answers EVERY verb unless told otherwise, which would turn a
#: POST to /sketch/ into a 302 instead of the 405 it must be. The same pin
#: primula, htmlview and memo carry on their own Office redirects.
SAFE_ONLY = ["get", "head", "options"]

urlpatterns = [
    # Drawings are an Office tab now. This app kept a flat list of its own with
    # no folders, no search and no sort — which is exactly what Office replaces,
    # and two answers to "what drawings are there" would drift apart. The name
    # survives so every existing reverse() still resolves.
    path("", RedirectView.as_view(http_method_names=SAFE_ONLY,
                                  pattern_name="office:section",
                                  query_string=True),
         {"section": "drawings"}, name="index"),
    path("new/", SketchCreateView.as_view(), name="create"),
    path("edit/<int:file_pk>/", SketchEditView.as_view(), name="edit"),
    path("save/<int:file_pk>/", sketch_save, name="save"),
    path("source/<int:file_pk>/", sketch_source, name="source"),
    path("delete/<int:file_pk>/", sketch_delete, name="delete"),
]
