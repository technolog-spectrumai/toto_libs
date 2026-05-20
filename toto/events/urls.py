from django.urls import path

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
    path('calendar/', EventCalendarView.as_view(), name='event_list'),
    path('event/create/', event_create, name='event_create'),
    path('event/<uuid:pk>/', EventDetailView.as_view(), name='event_detail'),
    path('event/<uuid:pk>/plan/', event_plan, name='event_plan'),
    path('invite/<uuid:pk>/respond/', invite_respond, name='invite_respond'),
    path('my-invites/', my_invites, name='my_invites'),
    path('api/availability/', event_availability_api, name='event_availability_api'),
]
