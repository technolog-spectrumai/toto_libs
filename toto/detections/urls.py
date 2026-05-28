from django.urls import path
from . import views

app_name = 'detections'

urlpatterns = [
    path('', views.DetectionListView.as_view(), name='detection-list'),
    path('new/', views.DetectionCreateView.as_view(), name='detection-create'),
    path('<uuid:pk>/help/', views.DetectionHelpView.as_view(), name='detection-help'),
    path('<uuid:pk>/', views.DetectionDetailView.as_view(), name='detection-detail'),
    path('manage/', views.DetectionDashboardView.as_view(), name='dashboard'),
]
