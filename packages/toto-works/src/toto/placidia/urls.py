from django.urls import path

from .views import (
    BountyBoardView, BountyDetailView, DatasetDetailView, DatasetListView,
    DatasetVersionDetailView, ReviewQueueView, contribute, freeze_dataset,
    review,
)

app_name = "placidia"

urlpatterns = [
    path("", BountyBoardView.as_view(), name="board"),
    path("bounty/<int:pk>/", BountyDetailView.as_view(), name="bounty"),
    path("bounty/<int:pk>/contribute/", contribute, name="contribute"),

    path("review/", ReviewQueueView.as_view(), name="review_queue"),
    path("review/<int:pk>/", review, name="review"),

    path("datasets/", DatasetListView.as_view(), name="datasets"),
    path("datasets/<int:pk>/", DatasetDetailView.as_view(), name="dataset"),
    path("datasets/<int:pk>/freeze/", freeze_dataset, name="dataset_freeze"),
    path("version/<int:pk>/", DatasetVersionDetailView.as_view(),
         name="dataset_version"),
]
