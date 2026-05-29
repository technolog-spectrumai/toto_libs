from django.urls import path
from . import views

app_name = "budget"

urlpatterns = [
    # Budget list/create
    path("", views.budget_list, name="list"),
    path("new/", views.budget_create, name="create"),
    # Budget detail/edit/delete
    path("<int:pk>/", views.budget_detail, name="detail"),
    path("<int:pk>/edit/", views.budget_update, name="update"),
    path("<int:pk>/delete/", views.budget_delete, name="delete"),
    # Account bindings
    path("<int:pk>/accounts/new/", views.budget_account_create, name="account_create"),
    path("accounts/<int:pk>/edit/", views.budget_account_update, name="account_update"),
    path("accounts/<int:pk>/delete/", views.budget_account_delete, name="account_delete"),
    # Items
    path("<int:pk>/items/new/", views.item_create, name="item_create"),
    path("items/<int:pk>/edit/", views.item_update, name="item_update"),
    path("items/<int:pk>/delete/", views.item_delete, name="item_delete"),
    # Stream types
    path("stream-types/", views.stream_type_list, name="stream_type_list"),
    path("stream-types/new/", views.stream_type_create, name="stream_type_create"),
    path("stream-types/<int:pk>/edit/", views.stream_type_update, name="stream_type_update"),
    path("stream-types/<int:pk>/delete/", views.stream_type_delete, name="stream_type_delete"),
    # Imports
    path("<int:pk>/imports/", views.budget_imports, name="imports"),
    path("<int:pk>/imports/<str:source>/preview/", views.budget_import_preview, name="import_preview"),
    path("<int:pk>/imports/<str:source>/run/", views.budget_import_run, name="import_run"),
    # Metrics
    path("<int:pk>/metrics/", views.budget_metrics_view, name="metrics"),
    # JSON
    path("<int:pk>/summary.json", views.budget_summary_data, name="summary_json"),
    path("<int:pk>/flow.json", views.budget_flow_data_view, name="flow_json"),
]
