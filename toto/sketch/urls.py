from django.urls import path
from .views import BoardListView, BoardDetailView, BoardSaveView, BoardExportSVGView, BoardCreateView, BoardDeleteView, SvgFileView
from toto.editor.views import save_file, delete_file

app_name = "sketch"

urlpatterns = [
    path("", BoardListView.as_view(), name="board_list"),
    path("create/", BoardCreateView.as_view(), name="board_create"),
    # SVG vault file editor
    path("svg/<int:file_pk>/",        SvgFileView.as_view(), name="svg_file_display"),
    path("svg/<int:file_pk>/save/",   save_file,             name="svg_file_save"),
    path("svg/<int:file_pk>/delete/", delete_file,           name="svg_file_delete"),
    # Boards (keep below svg/ to avoid slug collision)
    path("<str:board_id>/", BoardDetailView.as_view(), name="board_detail"),
    path("<str:board_id>/save/", BoardSaveView.as_view(), name="board_save"),
    path("<str:board_id>/delete/", BoardDeleteView.as_view(), name="board_delete"),
    path("<str:board_id>/export-svg/", BoardExportSVGView.as_view(), name="board_export_svg"),
]
