from django.urls import path
from . import views

app_name = "treasury"

urlpatterns = [
    path("", views.community_list, name="list"),
    path("<int:pk>/", views.community_detail, name="detail"),
    path("<int:pk>/flow.json", views.community_flow_json, name="flow_json"),
    path("<int:pk>/history.json", views.community_history_json, name="history_json"),
    path("revenue/", views.revenue_dashboard, name="revenue"),
    path("taxes/", views.taxes_overview, name="taxes"),
]
