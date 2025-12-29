from django.urls import path
from django.views.generic import RedirectView
from . import views

app_name = "finance"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="finance:account-list", permanent=False)),
    path("accounts/", views.finance.AccountListView.as_view(), name="account-list"),
    path("accounts/<uuid:pk>/", views.finance.AccountDetailView.as_view(), name="account-detail"),
    path("", views.assets.assets_list, name="assets_list"),  # /assets/
    path("<int:pk>/", views.assets.asset_detail, name="asset_detail"),  # /assets/5/
]
