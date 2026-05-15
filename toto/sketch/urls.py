from django.urls import path
from .views import BoardListView, BoardDetailView, BoardSaveView, BoardExportSVGView

app_name = "sketch"

urlpatterns = [
    path("", BoardListView.as_view(), name="board_list"),
    path("<str:board_id>/", BoardDetailView.as_view(), name="board_detail"),
    path("<str:board_id>/save/", BoardSaveView.as_view(), name="board_save"),
    path("<str:board_id>/export-svg/", BoardExportSVGView.as_view(), name="board_export_svg")
]
