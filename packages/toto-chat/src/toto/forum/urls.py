from django.urls import path

from . import views
from .views import (
    ChannelCreateView,
    ChannelDetailView,
    ChannelJoinView,
    ChannelLeaveView,
    ChannelListView,
    MessageSearchView,
)
from .api_views import (
    AudioUploadApiView,
    MessageAttachmentApiView,
    ChannelDetailApiView,
    ChannelJoinApiView,
    ChannelLeaveAllApiView,
    ChannelLeaveApiView,
    ChannelListApiView,
    ChannelMessagesApiView,
    ImageUploadApiView,
    MessageSearchApiView,
)

app_name = "forum"

# Auth/identity endpoints (health, apps, login, logout, me, me/mesh) are NOT here — they
# are not chat. They live in toto.api and the host mounts them at /api/, plus a legacy
# /forum/api/ alias for the shipped enigma desktop binary. See toto/api/urls.py.
urlpatterns = [
    # Template views
    path("", ChannelListView.as_view(), name="channel_list"),
    path("create/", ChannelCreateView.as_view(), name="channel_create"),
    path("search/", MessageSearchView.as_view(), name="message_search"),
    path("<slug:slug>/join/", ChannelJoinView.as_view(), name="channel_join"),
    path("<slug:slug>/leave/", ChannelLeaveView.as_view(), name="channel_leave"),
    # Room tabs — declared BEFORE the slug catch-all (the polls/urls.py trap:
    # "<slug>/files/" has two segments so it cannot collide today, but the
    # convention guards the next single-segment route somebody adds).
    path("<slug:slug>/files/", views.room_files, name="room_files"),
    path("<slug:slug>/polls/", views.room_polls, name="room_polls"),
    path("<slug:slug>/polls/new/", views.room_poll_create, name="room_poll_create"),
    path("<slug:slug>/polls/<slug:poll_slug>/vote/", views.room_poll_vote, name="room_poll_vote"),
    path("<slug:slug>/polls/<slug:poll_slug>/close/", views.room_poll_close, name="room_poll_close"),
    path("<slug:slug>/polls/<slug:poll_slug>/delete/", views.room_poll_delete, name="room_poll_delete"),
    path("<slug:slug>/stats/", views.room_stats, name="room_stats"),
    path("<slug:slug>/", ChannelDetailView.as_view(), name="channel_detail"),

    # JSON API
    path("api/search/", MessageSearchApiView.as_view(), name="api_message_search"),
    path(
        "api/messages/<uuid:message_id>/attachment/",
        MessageAttachmentApiView.as_view(),
        name="api_message_attachment",
    ),
    path("api/channels/", ChannelListApiView.as_view(), name="api_channel_list"),
    path("api/channels/leave-all/", ChannelLeaveAllApiView.as_view(), name="api_channel_leave_all"),
    path("api/channels/<slug:slug>/", ChannelDetailApiView.as_view(), name="api_channel_detail"),
    path("api/channels/<slug:slug>/messages/", ChannelMessagesApiView.as_view(), name="api_channel_messages"),
    path("api/channels/<slug:slug>/join/", ChannelJoinApiView.as_view(), name="api_channel_join"),
    path("api/channels/<slug:slug>/leave/", ChannelLeaveApiView.as_view(), name="api_channel_leave"),
    path("api/channels/<slug:slug>/upload/", ImageUploadApiView.as_view(), name="api_image_upload"),
    path("api/channels/<slug:slug>/upload-audio/", AudioUploadApiView.as_view(), name="api_audio_upload"),
]
