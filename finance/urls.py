from django.urls import path
from django.views.generic import RedirectView
from . import views

app_name = "finance"

urlpatterns = [
    #path("", RedirectView.as_view(pattern_name="finance:account-list", permanent=False)),
    #path("accounts/", views.accounts.AccountListView.as_view(), name="account-list"),
    path("accounts/<uuid:pk>/", views.accounts.AccountDetailView.as_view(), name="account-detail"),
    #path("assets/", views.assets.assets_list, name="assets_list"),  # /assets/
    path("assets/<int:pk>/", views.assets.asset_detail, name="asset-detail"),  # /assets/5/
    path("", views.dashboard.dashboard, name="dashboard"),
]
