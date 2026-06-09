from django.urls import path

from .views import neojson_editor_view, neojson_load_view, neojson_save_view

app_name = "neo_editor"

urlpatterns = [
    path("<int:file_pk>/", neojson_editor_view, name="neojson_editor"),
    path("<int:file_pk>/save/", neojson_save_view, name="neojson_save"),
    path("<int:file_pk>/load/", neojson_load_view, name="neojson_load"),
]
