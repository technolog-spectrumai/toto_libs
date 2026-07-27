from django.urls import path

from .views import (
    BibFileDisplayView,
    CsvFileDisplayView,
    HtmlFileDisplayView,
    JsonFileDisplayView,
    LatexFileDisplayView,
    TextFileDisplayView,
    XmlFileDisplayView,
    YamlFileDisplayView,
    delete_file,
    save_file,
)

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
    path("xml/<int:file_pk>/",          XmlFileDisplayView.as_view(),   name="xml_display"),
    path("xml/<int:file_pk>/save/",     save_file,                      name="xml_save"),
    path("xml/<int:file_pk>/delete/",   delete_file,                    name="xml_delete"),
    path("html/<int:file_pk>/",         HtmlFileDisplayView.as_view(),  name="html_display"),
    path("html/<int:file_pk>/save/",    save_file,                      name="html_save"),
    path("html/<int:file_pk>/delete/",  delete_file,                    name="html_delete"),
    path("csv/<int:file_pk>/",          CsvFileDisplayView.as_view(),   name="csv_display"),
    path("csv/<int:file_pk>/save/",     save_file,                      name="csv_save"),
    path("csv/<int:file_pk>/delete/",   delete_file,                    name="csv_delete"),
    path("latex/<int:file_pk>/",        LatexFileDisplayView.as_view(), name="latex_display"),
    path("latex/<int:file_pk>/save/",   save_file,                      name="latex_save"),
    path("latex/<int:file_pk>/delete/", delete_file,                    name="latex_delete"),
    path("bib/<int:file_pk>/",          BibFileDisplayView.as_view(),   name="bib_display"),
    path("bib/<int:file_pk>/save/",     save_file,                      name="bib_save"),
    path("bib/<int:file_pk>/delete/",   delete_file,                    name="bib_delete"),
]
