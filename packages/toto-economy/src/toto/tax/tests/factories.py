from toto.assets.testing import make_asset
"""Module-level factory helpers, per the tariffs tests' house pattern."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from toto.assets.models import Asset, AssetHolding
from toto.assets.prepaid import get_or_create_prepaid_account

from ..models import TaxRule

User = get_user_model()

GB = 2 ** 30


def make_user(name, **kwargs):
    return User.objects.create_user(name, password="pw", **kwargs)


def make_person(user):
    from toto.people.models import Person

    return Person.objects.create(user=user, display_name=user.username, slug=user.username)


def make_gas_asset(decimals=9):
    """The host billing asset under its default ticker, so rate_card helpers
    resolve it without setting overrides."""
    return make_asset(
        name="Gas", unit_name="ASR", decimals=decimals,
        max_supply_base_units=10 ** 15, active=True,
    )


def price_gb_day(value="0.5", asset=None):
    """Price the levy the way staff would — through the one price writer."""
    from toto.quota.metrics import registry
    from toto.tariffs.rate_card import upsert_price

    return upsert_price(registry.get("storage.gb_day"), Decimal(value), asset=asset)


def make_user_account(user, code, priority=0):
    from toto.assets.models import AccountType, LedgerAccount

    return LedgerAccount.objects.create(
        code=code, name=code, account_type=AccountType.USER,
        user=user, user_priority=priority, active=True,
    )


def fund_account(account, asset, base_units: int):
    from toto.assets.models import AssetHolding

    holding, _created = AssetHolding.objects.get_or_create(
        account=account, asset=asset, defaults={"balance_base_units": base_units},
    )
    if holding.balance_base_units != base_units:
        holding.balance_base_units = base_units
        holding.save()
    return holding



def fund_prepaid(user, asset, base_units: int):
    account, _created = get_or_create_prepaid_account(user)
    holding, _created = AssetHolding.objects.get_or_create(
        account=account, asset=asset, defaults={"balance_base_units": base_units},
    )
    if holding.balance_base_units != base_units:
        holding.balance_base_units = base_units
        holding.save()
    return account


def make_rule(metric_code="storage.gb_day", active=True, unit_label="GB"):
    """An armed levy rule. There is no allowance: everything held is billed."""
    return TaxRule.objects.create(
        metric_code=metric_code, unit_label=unit_label, active=active,
    )


_file_counter = [0]


def make_vault_file(owner, size_bytes, bucket=None):
    """A VaultFile whose recorded size is what the levy reads; content is a
    token byte so a real delete has something to unlink."""
    from toto.vault.models import VaultFile

    _file_counter[0] += 1
    name = f"levy-{owner.pk}-{_file_counter[0]}.txt"
    return VaultFile.objects.create(
        owner=owner, title=name, file_type="text",
        file=SimpleUploadedFile(name, b"x"),
        file_size_bytes=size_bytes, bucket=bucket,
    )
