from django.urls import path

from .views import JsonFileDisplayView, TextFileDisplayView, YamlFileDisplayView, delete_file, save_file

app_name = "editor"

urlpatterns = [
    path("text/<int:file_pk>/",        TextFileDisplayView.as_view(), name="text_display"),
    path("text/<int:file_pk>/save/",   save_file,                     name="text_save"),
    path("text/<int:file_pk>/delete/", delete_file,                   name="text_delete"),
    path("json/<int:file_pk>/",         JsonFileDisplayView.as_view(),  name="json_display"),
    path("json/<int:file_pk>/save/",    save_file,                      name="json_save"),
    path("json/<int:file_pk>/delete/",  delete_file,                    name="json_delete"),
    path("yaml/<int:file_pk>/",         YamlFileDisplayView.as_view(),  name="yaml_display"),
    path("yaml/<int:file_pk>/save/",    save_file,                      name="yaml_save"),
    path("yaml/<int:file_pk>/delete/",  delete_file,                    name="yaml_delete"),
]
