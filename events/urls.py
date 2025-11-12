from django.urls import path
from .views import EventCalendarView, EventDetailView


app_name = "events"

urlpatterns = [
    path('calendar/', EventCalendarView.as_view(), name='event_list'),
    path('event/details/<uuid:pk>/', EventDetailView.as_view(), name='event_detail'),
]
