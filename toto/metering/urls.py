from django.urls import path

from . import views

app_name = "metering"

urlpatterns = [
    # Dashboard
    path("", views.metrics, name="dashboard"),
    # Metrics
    path("metrics/", views.metric_list, name="metric_list"),
    path("metrics/new/", views.metric_create, name="metric_create"),
    path("metrics/<int:pk>/edit/", views.metric_update, name="metric_update"),
    path("metrics/<int:pk>/delete/", views.metric_delete, name="metric_delete"),
    # Usage events
    path("usage/", views.usage_list, name="usage_list"),
    path("usage/new/", views.usage_create, name="usage_create"),
    path("usage/<uuid:uid>/", views.usage_detail, name="usage_detail"),
    path("usage/<uuid:uid>/void/", views.usage_void, name="usage_void"),
    # Quotas
    path("quotas/", views.quota_list, name="quota_list"),
    path("quotas/new/", views.quota_create, name="quota_create"),
    path("quotas/<int:pk>/edit/", views.quota_update, name="quota_update"),
    path("quotas/<int:pk>/delete/", views.quota_delete, name="quota_delete"),
    # API
    path("api/record/", views.api_record_usage, name="api_record_usage"),
]
