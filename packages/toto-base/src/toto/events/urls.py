from django.urls import path

from .api_views import (
    EventListApiView,
    EventDetailApiView,
    EventInviteApiView,
    FormDataApiView,
    MyInvitesApiView,
    InviteRespondApiView,
)
from .views import (
    EventCalendarView,
    EventDetailView,
    event_availability_api,
    event_create,
    event_plan,
    invite_respond,
    my_invites,
)


app_name = "events"

urlpatterns = [
    # Enigma JSON API
    path("api/enigma/list/", EventListApiView.as_view(), name="api_list"),
    path("api/enigma/form-data/", FormDataApiView.as_view(), name="api_form_data"),
    path("api/enigma/my-invites/", MyInvitesApiView.as_view(), name="api_my_invites"),
    path("api/enigma/<uuid:pk>/", EventDetailApiView.as_view(), name="api_detail"),
    path("api/enigma/<uuid:pk>/invite/", EventInviteApiView.as_view(), name="api_invite"),
    path("api/enigma/invites/<uuid:pk>/respond/", InviteRespondApiView.as_view(), name="api_invite_respond"),

    path('calendar/', EventCalendarView.as_view(), name='event_list'),
    path('event/create/', event_create, name='event_create'),
    path('event/<uuid:pk>/', EventDetailView.as_view(), name='event_detail'),
    path('event/<uuid:pk>/plan/', event_plan, name='event_plan'),
    path('invite/<uuid:pk>/respond/', invite_respond, name='invite_respond'),
    path('my-invites/', my_invites, name='my_invites'),
    path('api/availability/', event_availability_api, name='event_availability_api'),
]
