from django.urls import path

from . import views

app_name = "assets"

urlpatterns = [
    path("", views.asset_list, name="asset_list"),
    path("assets/<int:pk>/", views.asset_detail, name="asset_detail"),
    path("accounts/", views.account_list, name="account_list"),
    path("accounts/<int:pk>/", views.account_detail, name="account_detail"),
    path("transactions/", views.transaction_list, name="transaction_list"),
    path("transactions/<int:pk>/", views.transaction_detail, name="transaction_detail"),
    path("chain/", views.chain_verify, name="chain_verify"),
    path("flow/", views.ledger_flow, name="ledger_flow"),
    path("flow/data/", views.ledger_flow_data, name="ledger_flow_data"),
]
