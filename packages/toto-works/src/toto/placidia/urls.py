from django.urls import path

from . import staff_views
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

    # ── staff: running the programme ───────────────────────────────────────
    path("manage/", staff_views.manage, name="manage"),
    path("manage/campaign/new/", staff_views.campaign_create, name="campaign_create"),
    path("manage/bounty/new/", staff_views.bounty_create, name="bounty_create"),
    path("manage/bounty/<int:pk>/edit/", staff_views.bounty_edit, name="bounty_edit"),
    path("manage/bounty/<int:pk>/delete/", staff_views.bounty_delete, name="bounty_delete"),
    path("manage/bounty/<int:pk>/toggle/", staff_views.bounty_toggle, name="bounty_toggle"),
    path("manage/rewards/distribute/", staff_views.distribute_rewards,
         name="distribute_rewards"),
]
