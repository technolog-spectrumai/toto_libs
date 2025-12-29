from django.urls import path
from . import views

app_name = "finance"

urlpatterns = [
    # DASHBOARD (static, server-rendered)
    path("", views.dashboard.dashboard, name="dashboard"),
    path("accounts/<uuid:pk>/", views.accounts.AccountDetailView.as_view(), name="account-detail"),
    path("assets/<int:pk>/", views.assets.asset_detail, name="asset-detail"),
]
