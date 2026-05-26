from django.urls import path

from . import views

app_name = "assets"

urlpatterns = [
    path("", views.asset_list, name="asset_list"),
    path("assets/create/", views.asset_create, name="asset_create"),
    path("assets/<int:pk>/", views.asset_detail, name="asset_detail"),
    path("assets/<int:pk>/distribute/", views.asset_distribute, name="asset_distribute"),
    path("objects/<int:object_id>/tokenize/", views.tokenization_create_for_object, name="tokenization_create_for_object"),
    path("tokenizations/<int:pk>/default/", views.tokenization_default, name="tokenization_default"),
    path("accounts/", views.account_list, name="account_list"),
    path("accounts/<int:pk>/", views.account_detail, name="account_detail"),
    path("transactions/", views.transaction_list, name="transaction_list"),
    path("transactions/<int:pk>/", views.transaction_detail, name="transaction_detail"),
    path("chain/", views.chain_verify, name="chain_verify"),
    path("flow/", views.ledger_flow, name="ledger_flow"),
    path("flow/data/", views.ledger_flow_data, name="ledger_flow_data"),
    path("obligations/<int:pk>/fulfill/", views.obligation_fulfill, name="obligation_fulfill"),
    path("wallet/", views.wallet, name="wallet"),
    path("wallet/pin/", views.wallet_pin_set, name="wallet_pin_set"),
    path("wallet/pin/verify/", views.wallet_pin_verify, name="wallet_pin_verify"),
    path("agreements/", views.agreement_list, name="agreement_list"),
    path("agreements/create/", views.agreement_create, name="agreement_create"),
    path("agreements/<int:pk>/", views.agreement_detail, name="agreement_detail"),
    path("contracts/", views.contract_list, name="contract_list"),
    path("contracts/new/", views.contract_create, name="contract_create"),
    path("contracts/<uuid:uuid>/", views.contract_detail, name="contract_detail"),
    path("contracts/<uuid:uuid>/edit/", views.contract_update, name="contract_update"),
    path("contracts/<uuid:uuid>/cytoscape.json", views.contract_cytoscape_json, name="contract_cytoscape_json"),
]
