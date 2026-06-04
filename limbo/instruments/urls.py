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
    path("<int:pk>/recipient/add/", views.recipient_add, name="recipient_add"),
    path("<int:pk>/recipient/<int:recipient_pk>/remove/", views.recipient_remove, name="recipient_remove"),

    # Staking
    path("staking/create/", views.staking_create, name="staking_create"),
    path("<int:pk>/staking/stake/", views.staking_stake, name="staking_stake"),
    path("<int:pk>/staking/unstake/", views.staking_unstake, name="staking_unstake"),

    # Amortization
    path("amortizations/", views.amortization_list, name="amortization_list"),
    path("amortizations/create/", views.amortization_create, name="amortization_create"),
    path("amortizations/<int:pk>/", views.amortization_detail, name="amortization_detail"),
    path("amortizations/<int:pk>/activate/", views.amortization_activate, name="amortization_activate"),
    path("amortizations/<int:pk>/pause/", views.amortization_pause, name="amortization_pause"),
    path("amortizations/<int:pk>/resume/", views.amortization_resume, name="amortization_resume"),
    path("amortizations/<int:pk>/cancel/", views.amortization_cancel, name="amortization_cancel"),
    path("amortizations/<int:pk>/amortize/", views.amortization_amortize, name="amortization_amortize"),
]
