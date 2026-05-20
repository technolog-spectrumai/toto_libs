from django.urls import path
from .views import EventCalendarView, EventDetailView, event_create


app_name = "events"

urlpatterns = [
    path('calendar/', EventCalendarView.as_view(), name='event_list'),
    path('event/create/', event_create, name='event_create'),
    path('event/details/<uuid:pk>/', EventDetailView.as_view(), name='event_detail'),
]
