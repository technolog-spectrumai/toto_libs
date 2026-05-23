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

    # Option
    path("options/create/", views.option_create, name="option_create"),
    path("<int:pk>/option/exercise/", views.option_exercise, name="option_exercise"),

    # Vesting
    path("vesting/create/", views.vesting_create, name="vesting_create"),

    # Revenue share
    path("revenue-share/create/", views.revenue_share_create, name="revenue_share_create"),

    # Staking
    path("staking/create/", views.staking_create, name="staking_create"),
    path("<int:pk>/staking/stake/", views.staking_stake, name="staking_stake"),
    path("<int:pk>/staking/unstake/", views.staking_unstake, name="staking_unstake"),

    # Subscription
    path("subscriptions/create/", views.subscription_create, name="subscription_create"),
    path("<int:pk>/subscription/activate/", views.subscription_activate, name="subscription_activate"),
    path("<int:pk>/subscription/cancel/", views.subscription_cancel, name="subscription_cancel"),
    path("<int:pk>/subscription/pause/", views.subscription_pause, name="subscription_pause"),
    path("<int:pk>/subscription/resume/", views.subscription_resume, name="subscription_resume"),

    # Lease
    path("leases/", views.lease_list, name="lease_list"),
    path("leases/create/", views.lease_create, name="lease_create"),
    path("leases/metrics/create/", views.lease_metric_create, name="lease_metric_create"),
    path("leases/charges/<int:charge_pk>/collect/", views.lease_charge_collect, name="lease_charge_collect"),
    path("leases/charges/<int:charge_pk>/waive/", views.lease_charge_waive, name="lease_charge_waive"),
    path("leases/<int:pk>/", views.lease_detail, name="lease_detail"),
    path("leases/<int:pk>/activate/", views.lease_activate, name="lease_activate"),
    path("leases/<int:pk>/cancel/", views.lease_cancel, name="lease_cancel"),
    path("leases/<int:pk>/charge-fixed/", views.lease_charge_fixed, name="lease_charge_fixed"),
    path("leases/<int:pk>/tariff/create/", views.lease_tariff_create, name="lease_tariff_create"),
    path("leases/<int:pk>/charge/create/", views.lease_charge_create, name="lease_charge_create"),
]
