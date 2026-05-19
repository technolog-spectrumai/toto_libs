from django.urls import path

from . import views

app_name = "assets"

urlpatterns = [
    path("", views.asset_list, name="asset_list"),
    path("assets/<int:pk>/", views.asset_detail, name="asset_detail"),
    path("objects/<int:object_id>/tokenize/", views.tokenization_create_for_object, name="tokenization_create_for_object"),
    path("accounts/", views.account_list, name="account_list"),
    path("accounts/<int:pk>/", views.account_detail, name="account_detail"),
    path("transactions/", views.transaction_list, name="transaction_list"),
    path("transactions/<int:pk>/", views.transaction_detail, name="transaction_detail"),
    path("chain/", views.chain_verify, name="chain_verify"),
    path("exchange/", views.exchange_center, name="exchange_center"),
    path("exchange/requests/new/", views.exchange_request_create, name="exchange_request_create"),
    path("exchange/requests/<int:pk>/accept/", views.exchange_request_accept, name="exchange_request_accept"),
    path("exchange/requests/<int:pk>/reject/", views.exchange_request_reject, name="exchange_request_reject"),
    path("flow/", views.ledger_flow, name="ledger_flow"),
    path("flow/data/", views.ledger_flow_data, name="ledger_flow_data"),
    path("obligations/<int:pk>/fulfill/", views.obligation_fulfill, name="obligation_fulfill"),
    path("wallet/", views.wallet, name="wallet"),
    path("wallet/pin/", views.wallet_pin_set, name="wallet_pin_set"),
    path("wallet/pin/verify/", views.wallet_pin_verify, name="wallet_pin_verify"),
]
