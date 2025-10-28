from django.urls import path
from .views import (
    BoardDetailView, BoardListView
)

app_name = 'kanban'

urlpatterns = [
    path('', BoardListView.as_view(), name='board_list'),
    path('board/<int:pk>/', BoardDetailView.as_view(), name='board')
]
