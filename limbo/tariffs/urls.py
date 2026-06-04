from django.urls import path

from . import views

app_name = "tariffs"

urlpatterns = [
    # Metering overview (raw usage events from all apps)
    path("metering/", views.metering_overview, name="metering_overview"),
    # Tariffs
    path("", views.tariff_list, name="tariff_list"),
    path("new/", views.tariff_create, name="tariff_create"),
    path("<uuid:uuid>/", views.tariff_detail, name="tariff_detail"),
    path("<uuid:uuid>/edit/", views.tariff_edit, name="tariff_edit"),
    path("<uuid:uuid>/items/new/", views.tariff_item_create, name="tariff_item_create"),
    path("<uuid:uuid>/simulate/", views.tariff_simulate, name="tariff_simulate"),
    # Tariff items
    path("items/<int:pk>/edit/", views.tariff_item_edit, name="tariff_item_edit"),
    # Usage
    path("usage/", views.usage_list, name="usage_list"),
    path("usage/new/", views.usage_create, name="usage_create"),
    path("usage/<uuid:uuid>/", views.usage_detail, name="usage_detail"),
    path("usage/<uuid:uuid>/post/", views.usage_post, name="usage_post"),
    # Metrics
    path("metrics/", views.metrics, name="metrics"),
    # JSON APIs
    path("api/rate/", views.api_rate, name="api_rate"),
    path("api/post/", views.api_post, name="api_post"),
    path("api/metrics/", views.api_metrics, name="api_metrics"),
    # Usage statement billing handoff
    path("apply-usage-statement/", views.apply_usage_statement, name="apply_usage_statement"),
    path("apply-usage-statement/preview.json", views.preview_usage_statement_json, name="preview_usage_statement_json"),
    path("apply-usage-statement/price.json", views.price_usage_statement_json, name="price_usage_statement_json"),
    path("applications/", views.tariff_application_list, name="tariff_application_list"),
    path("applications/<uuid:uid>/", views.tariff_application_detail, name="tariff_application_detail"),
]
