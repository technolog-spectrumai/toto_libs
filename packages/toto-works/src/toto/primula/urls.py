from django.urls import path

from .views import (
    SheetIndexView,
    SheetCreateView,
    SheetEditView,
    SheetVersionsView,
    sheet_save,
    sheet_delete,
    sheet_restore,
)

app_name = "primula"

urlpatterns = [
    path("", SheetIndexView.as_view(), name="index"),
    path("new/", SheetCreateView.as_view(), name="create"),
    path("edit/<int:file_pk>/", SheetEditView.as_view(), name="edit"),
    path("save/<int:file_pk>/", sheet_save, name="save"),
    path("delete/<int:file_pk>/", sheet_delete, name="delete"),
    path("versions/<int:file_pk>/", SheetVersionsView.as_view(), name="versions"),
    path("restore/<int:file_pk>/<int:version_pk>/", sheet_restore, name="restore"),
]
