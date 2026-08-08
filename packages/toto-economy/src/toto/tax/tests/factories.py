"""Module-level factory helpers, per the tariffs tests' house pattern."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from toto.assets.models import Asset, AssetHolding
from toto.assets.prepaid import get_or_create_prepaid_account
from toto.quota.levy import LevyProvider

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
    return Asset.objects.create(
        name="Gas", unit_name="ASR", decimals=decimals,
        total_supply_base_units=10 ** 15, active=True,
    )


def price_gb_day(value="0.5", asset=None):
    """Price the levy the way staff would — through the one price writer."""
    from toto.quota.metrics import registry
    from toto.tariffs.rate_card import upsert_price

    return upsert_price(registry.get("storage.gb_day"), Decimal(value), asset=asset)


def fund_prepaid(user, asset, base_units: int):
    account, _created = get_or_create_prepaid_account(user)
    holding, _created = AssetHolding.objects.get_or_create(
        account=account, asset=asset, defaults={"balance_base_units": base_units},
    )
    if holding.balance_base_units != base_units:
        holding.balance_base_units = base_units
        holding.save()
    return account


def make_rule(allowance="1", metric_code="storage.gb_day", active=True):
    return TaxRule.objects.create(
        metric_code=metric_code, allowance=Decimal(allowance),
        unit_label="GB", active=active,
    )


_file_counter = [0]


def make_vault_file(owner, size_bytes, bucket=None):
    """A VaultFile whose recorded size is what the levy reads; content is a
    token byte so enforcement has something real to unlink."""
    from toto.vault.models import VaultFile

    _file_counter[0] += 1
    name = f"levy-{owner.pk}-{_file_counter[0]}.txt"
    return VaultFile.objects.create(
        owner=owner, title=name, file_type="text",
        file=SimpleUploadedFile(name, b"x"),
        file_size_bytes=size_bytes, bucket=bucket,
    )


class FakeRng:
    """A deterministic stand-in for random: shuffles into pk order."""

    def shuffle(self, rows):
        rows.sort()


class FakeProvider(LevyProvider):
    """An in-memory resource for arrears tests: holdings shed in fixed chunks."""

    code = "fake.resource"
    metric_code = "storage.gb_day"
    raw_per_unit = GB

    def __init__(self, holdings=None, chunk=GB, protect_all=False):
        self.holdings = dict(holdings or {})
        self.chunk = chunk
        self.protect_all = protect_all
        self.enforce_calls = []

    def sample(self):
        yield from self.holdings.items()

    def measure(self, user):
        return self.holdings.get(user.pk, 0)

    def enforce(self, user, target_raw, *, rng=None, on_deleted=None, on_skipped=None):
        from toto.quota.levy import EnforcementResult

        self.enforce_calls.append((user.pk, target_raw))
        result = EnforcementResult()
        remaining = self.holdings.get(user.pk, 0)
        item = 0
        while remaining > target_raw:
            item += 1
            info = {"pk": item, "title": f"item-{item}", "key": f"item-{item}",
                    "bucket": "", "size": self.chunk}
            if self.protect_all:
                result.skipped_count += 1
                if on_skipped is not None:
                    on_skipped(info)
                break
            remaining -= self.chunk
            result.deleted_count += 1
            result.deleted_raw += self.chunk
            if on_deleted is not None:
                on_deleted(info)
        self.holdings[user.pk] = remaining
        result.final_raw = remaining
        result.reached_target = remaining <= target_raw
        return result
