from django.urls import path
from .views import BoardDetailView

urlpatterns = [
    path('board/<int:pk>/', BoardDetailView.as_view(), name='board-detail'),
]
