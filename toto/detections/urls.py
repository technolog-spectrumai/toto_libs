from django.urls import path
from . import views
from .api_views import DetectionsListApiView, MissionsListApiView, DetectionHelpApiView

app_name = 'detections'

urlpatterns = [
    path('api/enigma/list/', DetectionsListApiView.as_view(), name='api_enigma_list'),
    path('api/enigma/missions/', MissionsListApiView.as_view(), name='api_enigma_missions'),
    path('api/enigma/<uuid:pk>/help/', DetectionHelpApiView.as_view(), name='api_enigma_help'),
    path('', views.DetectionListView.as_view(), name='detection-list'),
    path('new/', views.DetectionCreateView.as_view(), name='detection-create'),
    path('<uuid:pk>/help/', views.DetectionHelpView.as_view(), name='detection-help'),
    path('<uuid:pk>/', views.DetectionDetailView.as_view(), name='detection-detail'),
    path('manage/', views.DetectionDashboardView.as_view(), name='dashboard'),
    path('api/export/', views.api_export_layers, name='api_export_layers'),
    path('api/run/<int:run_id>/status/', views.api_export_run_status, name='api_export_run_status'),
]
