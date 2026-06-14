from django.urls import path
from .views import ChannelDetailView, ChannelJoinView, ChannelLeaveView, ChannelListView
from .api_views import HealthApiView, LoginApiView, LogoutApiView, MeApiView, MeshMeApiView, ChannelListApiView, ChannelDetailApiView, ChannelJoinApiView, ChannelLeaveApiView, ChannelLeaveAllApiView, ImageUploadApiView, AudioUploadApiView, ChannelPinsApiView, ChannelPinUnpinApiView

app_name = "telegraph"

urlpatterns = [
    # Template views
    path("", ChannelListView.as_view(), name="channel_list"),
    path("<slug:slug>/join/", ChannelJoinView.as_view(), name="channel_join"),
    path("<slug:slug>/leave/", ChannelLeaveView.as_view(), name="channel_leave"),
    path("<slug:slug>/", ChannelDetailView.as_view(), name="channel_detail"),

    # JSON API
    path("api/health/", HealthApiView.as_view(), name="api_health"),
    path("api/login/", LoginApiView.as_view(), name="api_login"),
    path("api/logout/", LogoutApiView.as_view(), name="api_logout"),
    path("api/me/", MeApiView.as_view(), name="api_me"),
    path("api/me/mesh/", MeshMeApiView.as_view(), name="api_me_mesh"),
    path("api/channels/", ChannelListApiView.as_view(), name="api_channel_list"),
    path("api/channels/leave-all/", ChannelLeaveAllApiView.as_view(), name="api_channel_leave_all"),
    path("api/channels/<slug:slug>/", ChannelDetailApiView.as_view(), name="api_channel_detail"),
    path("api/channels/<slug:slug>/join/", ChannelJoinApiView.as_view(), name="api_channel_join"),
    path("api/channels/<slug:slug>/leave/", ChannelLeaveApiView.as_view(), name="api_channel_leave"),
    path("api/channels/<slug:slug>/upload/", ImageUploadApiView.as_view(), name="api_image_upload"),
    path("api/channels/<slug:slug>/upload-audio/", AudioUploadApiView.as_view(), name="api_audio_upload"),
    path("api/channels/<slug:slug>/pins/", ChannelPinsApiView.as_view(), name="api_channel_pins"),
    path("api/channels/<slug:slug>/pins/<uuid:pin_id>/unpin/", ChannelPinUnpinApiView.as_view(), name="api_channel_pin_unpin"),
]
