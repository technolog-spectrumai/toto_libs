from django.urls import path
from .views import EventCalendarView


app_name = "events"

urlpatterns = [
    path('', EventCalendarView.as_view(), name='event_list'),
]
