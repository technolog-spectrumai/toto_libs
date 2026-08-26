from django.urls import path

from . import views
from .api_views import (
    WalletSummaryApiView, MovementsApiView, PinVerifyApiView,
)

app_name = "assets"

urlpatterns = [
    # Machine-readable, signed, read-only. See views.attestation.
    path("attestation.json", views.attestation, name="attestation"),
    # Enigma JSON API
    path("api/wallet/summary/", WalletSummaryApiView.as_view(), name="api_wallet_summary"),
    path("api/wallet/movements/", MovementsApiView.as_view(), name="api_movements"),
    path("api/wallet/pin/verify/", PinVerifyApiView.as_view(), name="api_pin_verify"),


    path("", views.asset_list, name="asset_list"),
    path("assets/create/", views.asset_create, name="asset_create"),
    path("settlement/", views.settlement_choose, name="settlement_choose"),
    path("faucets/", views.faucet_list, name="faucet_list"),
    path("faucets/new/", views.faucet_create, name="faucet_create"),
    path("faucets/<int:pk>/toggle/", views.faucet_toggle, name="faucet_toggle"),
    path("faucets/<int:pk>/members/add/", views.faucet_member_add,
         name="faucet_member_add"),
    path("faucets/members/<int:pk>/remove/", views.faucet_member_remove,
         name="faucet_member_remove"),
    path("assets/<int:pk>/", views.asset_detail, name="asset_detail"),
    path("assets/<int:pk>/distribute/", views.asset_distribute, name="asset_distribute"),
    path("accounts/", views.account_list, name="account_list"),
    path("accounts/<int:pk>/", views.account_detail, name="account_detail"),
    path("transactions/", views.transaction_list, name="transaction_list"),
    path("transactions/<int:pk>/", views.transaction_detail, name="transaction_detail"),
    path("chain/", views.chain_verify, name="chain_verify"),
    path("flow/", views.ledger_flow, name="ledger_flow"),
    path("flow/data/", views.ledger_flow_data, name="ledger_flow_data"),
    path("wallet/", views.wallet, name="wallet"),
    path("accounts/<int:pk>/priority/", views.set_account_priority, name="set_account_priority"),
    # The decorated ledger: one account's movements plus the notes beside them.
    path("accounts/<int:pk>/ledger/", views.account_ledger, name="account_ledger"),
    path("accounts/<int:pk>/ledger/<int:entry_id>/note/", views.entry_comment, name="entry_comment"),
    path("accounts/<int:pk>/ledger/<int:entry_id>/tag/", views.entry_tag_add, name="entry_tag_add"),
    path("accounts/<int:pk>/ledger/<int:entry_id>/untag/", views.entry_tag_remove, name="entry_tag_remove"),
    path("wallet/pin/", views.wallet_pin_set, name="wallet_pin_set"),
    path("wallet/pin/verify/", views.wallet_pin_verify, name="wallet_pin_verify"),
    path("wallet/authorizations/", views.authorization_list, name="authorization_list"),
]
