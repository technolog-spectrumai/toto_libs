"""Shared set-up for the mana tests: a master host with three full pools."""

from decimal import Decimal
from itertools import count

from django.test import override_settings

from toto.assets.testing import TEST_ISSUER_KEY

#: The settings every mana test runs under: this host may issue currencies.
MASTER = dict(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY, ASSETS_MONETARY_MASTER=True)
master = override_settings(**MASTER)

_ref = count()


def platform():
    from toto.core.models import Platform

    Platform.objects.get_or_create(
        site_name="Test",
        defaults={"author": "t", "publication_year": 2026, "active": True})


def economy():
    """Issuer, core currencies, the three pools — what any ingress guarantees."""
    from toto.assets.services.bootstrap import bootstrap_economy

    platform()
    bootstrap_economy()


def seed_prices():
    """Price every mapped metric in its pool — ingress_mana's seed, without the
    command, so a unit test does not depend on a management command."""
    from toto.mana import colours, services
    from toto.quota.metrics import registry
    from toto.tariffs.rate_card import upsert_price

    for code, price in colours.PRICES.items():
        metric, asset = registry.get(code), services.asset_for(code)
        if metric is not None and asset is not None:
            upsert_price(metric, price, asset=asset)


def held(user, role) -> Decimal:
    """A member's pool, in display units."""
    from toto.mana import services

    pool = services.pools()[role]
    return Decimal(services.balance_base_units(user, pool)) / 10 ** pool.asset.decimals


def spend(user, role, amount):
    """An ordinary transfer out of a pool — what a charge does to it."""
    from toto.assets.prepaid import get_or_create_prepaid_account
    from toto.assets.services.assets import transfer_asset
    from toto.mana import services

    pool = services.pools()[role]
    account, _ = get_or_create_prepaid_account(user)
    transfer_asset(asset=pool.asset, sender_account=account,
                   receiver_account=pool.asset.reserve_account,
                   amount=Decimal(amount), reference=f"test-spend-{next(_ref)}")
