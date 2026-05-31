from django.urls import path
from .views import BoardListView, BoardDetailView, BoardSaveView, BoardExportSVGView, BoardCreateView, BoardDeleteView

app_name = "sketch"

urlpatterns = [
    path("", BoardListView.as_view(), name="board_list"),
    path("create/", BoardCreateView.as_view(), name="board_create"),
    path("<str:board_id>/", BoardDetailView.as_view(), name="board_detail"),
    path("<str:board_id>/save/", BoardSaveView.as_view(), name="board_save"),
    path("<str:board_id>/delete/", BoardDeleteView.as_view(), name="board_delete"),
    path("<str:board_id>/export-svg/", BoardExportSVGView.as_view(), name="board_export_svg"),
]
