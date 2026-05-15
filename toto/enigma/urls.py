from django.urls import path
from .views import RoomDetailView, RoomJoinView, RoomLeaveView, RoomListView, RoomInviteAgentView
from .api_views import HealthApiView, LoginApiView, LogoutApiView, MeApiView, RoomListApiView, RoomDetailApiView, RoomJoinApiView, RoomLeaveApiView, RoomLeaveAllApiView

app_name = "enigma"

urlpatterns = [
    # Template views
    path("", RoomListView.as_view(), name="room_list"),
    path("<slug:slug>/join/", RoomJoinView.as_view(), name="room_join"),
    path("<slug:slug>/leave/", RoomLeaveView.as_view(), name="room_leave"),
    path("<slug:slug>/", RoomDetailView.as_view(), name="room_detail"),
    path("<slug:slug>/invite-agent/", RoomInviteAgentView.as_view(), name="room_invite_agent"),

    # JSON API
    path("api/health/", HealthApiView.as_view(), name="api_health"),
    path("api/login/", LoginApiView.as_view(), name="api_login"),
    path("api/logout/", LogoutApiView.as_view(), name="api_logout"),
    path("api/me/", MeApiView.as_view(), name="api_me"),
    path("api/rooms/", RoomListApiView.as_view(), name="api_room_list"),
    path("api/rooms/leave-all/", RoomLeaveAllApiView.as_view(), name="api_room_leave_all"),
    path("api/rooms/<slug:slug>/", RoomDetailApiView.as_view(), name="api_room_detail"),
    path("api/rooms/<slug:slug>/join/", RoomJoinApiView.as_view(), name="api_room_join"),
    path("api/rooms/<slug:slug>/leave/", RoomLeaveApiView.as_view(), name="api_room_leave"),
]
