# forum/urls.py
from django.urls import path
from . import views


app_name = "forum"

urlpatterns = [
    path('', views.room_list, name='room_list'),
    path('room/<int:room_id>/', views.room_view, name='room_view'),
]
