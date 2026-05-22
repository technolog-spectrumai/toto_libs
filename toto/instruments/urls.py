from django.urls import path

from . import views

app_name = "instruments"

urlpatterns = [
    # Instrument list / detail / create
    path("", views.instrument_list, name="instrument_list"),
    path("create/", views.instrument_create, name="instrument_create"),
    path("<int:pk>/", views.instrument_detail, name="instrument_detail"),

    # Escrow
    path("escrows/create/", views.escrow_create, name="escrow_create"),
    path("escrows/<int:pk>/fund/", views.escrow_fund, name="escrow_fund"),
    path("escrows/<int:pk>/release/", views.escrow_release, name="escrow_release"),
    path("escrows/<int:pk>/refund/", views.escrow_refund, name="escrow_refund"),

    # Forward
    path("forwards/create/", views.forward_create, name="forward_create"),
    path("<int:pk>/forward/activate/", views.forward_activate, name="forward_activate"),

    # Future markets
    path("future-markets/", views.future_market_list, name="future_market_list"),
    path("future-markets/create/", views.future_market_create, name="future_market_create"),
    path("futures/create/", views.future_contract_create, name="future_contract_create"),

    # Timelock
    path("timelocks/create/", views.timelock_create, name="timelock_create"),
    path("<int:pk>/timelock/fund/", views.timelock_fund, name="timelock_fund"),
    path("<int:pk>/timelock/release/", views.timelock_release, name="timelock_release"),

    # Vesting
    path("vesting/create/", views.vesting_create, name="vesting_create"),

    # Revenue share
    path("revenue-share/create/", views.revenue_share_create, name="revenue_share_create"),

    # Staking
    path("staking/create/", views.staking_create, name="staking_create"),
    path("<int:pk>/staking/stake/", views.staking_stake, name="staking_stake"),
    path("<int:pk>/staking/unstake/", views.staking_unstake, name="staking_unstake"),
]
