"""
ingress_treasury — seed budget stream types, community treasury accounts, base tariffs, and demo budgets.

Run:
  python manage.py ingress_treasury
  python manage.py ingress_treasury --full

What this does:
  1. Syncs BudgetStreamType rows from the registry.
  2. Ensures every Community has a treasury LedgerAccount.
  3. Creates a community-specific tariff "OUR-THING-COMMUNITY-PLAN" for the
     first "Our Thing Inc." community — with 20 % discount vs. platform rates.
  4. Creates a demo Budget for "Our Thing Inc." with operating and reserve accounts.
  5. (--full) Funds the admin user's prepaid accounts so they can test all
     metered features without hitting zero-balance errors.

Run AFTER: ingress_tariffs, ingress_vault, ingress_vod, ingress_ravioli,
           ingress_steven, ingress_transcription (they set up the platform
           tariffs and metrics that the community plan overrides).
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model

from toto.assets.models import AccountType, LedgerAccount, to_base_units
from toto.ingress import IngressCommand
from toto.socialhub.treasury import get_or_create_treasury_account
from toto.tariffs.models import (
    BillingMetric,
    RoundingMode,
    Tariff,
    TariffItem,
    TariffStatus,
)

User = get_user_model()

# Admin gets this many tokens of each type so all test flows pass easily.
_ADMIN_FUND_AMOUNT = Decimal("500000")

# Community plan: 20 % cheaper than platform defaults.
_COMMUNITY_DISCOUNT = Decimal("0.80")

_COMMUNITY_TARIFF_CODE = "OUR-THING-COMMUNITY-PLAN"

# metric_code → (platform_price, asset_unit_name, unit_quantity)
# Mirrors the prices in ingress_tariffs.py so the community gets a clear discount.
_COMMUNITY_ITEMS = [
    # vault / storage
    ("storage.request",     "0.1",  "STORAGE_TOKEN", 1),
    ("storage.transfer_mb", "1.0",  "STORAGE_TOKEN", 1),
    ("storage.mb_hour",     "0.01", "STORAGE_TOKEN", 1),
    ("storage.gb_hour",     "10.0", "STORAGE_TOKEN", 1),
    # steven / AI
    ("ai.input_tokens",     "1.0",  "AI_TOKEN",      1000),
    ("ai.output_tokens",    "3.0",  "AI_TOKEN",      1000),
    ("ai.requests",         "0.5",  "AI_TOKEN",      1),
    # ravioli / graph
    ("neo4j.query",         "0.5",  "GRAPH_TOKEN",   1),
    ("neo4j.node",          "0.1",  "GRAPH_TOKEN",   1),
    ("neo4j.relationship",  "0.05", "GRAPH_TOKEN",   1),
    # compute
    ("compute.second",      "0.1",  "COMPUTE_TOKEN", 1),
    # api
    ("api.request",         "0.1",  "API_TOKEN",     1),
    ("api.webhook",         "0.5",  "API_TOKEN",     1),
    # ocr
    ("ocr.image",           "0.5",  "COMPUTE_TOKEN", 1),
    ("ocr.page",            "0.1",  "COMPUTE_TOKEN", 1),
    # texlab
    ("texlab.compile",      "1.0",  "COMPUTE_TOKEN", 1),
    ("texlab.page",         "0.1",  "COMPUTE_TOKEN", 1),
    # mandragora (notebooks)
    ("mandragora.execution",      "0.1", "COMPUTE_TOKEN", 1),
    ("mandragora.compute_second", "0.1", "COMPUTE_TOKEN", 1),
    # vod
    ("vod.upload_mb",       "1.0",  "STORAGE_TOKEN", 1),
    ("vod.stream_mb",       "1.0",  "STORAGE_TOKEN", 1),
    # transcription
    ("transcription.request", "0.5", "AI_TOKEN", 1),
    ("transcription.second",  "0.1", "AI_TOKEN", 1),
    # weather
    ("weather.request",     "0.1",  "API_TOKEN",     1),
    # kanban
    ("kanban.task",         "0.1",  "API_TOKEN",     1),
    ("kanban.api_request",  "0.1",  "API_TOKEN",     1),
]


def _get_rev_account(asset_unit_name: str) -> "LedgerAccount | None":
    code_map = {
        "AI_TOKEN":      "REV-AI",
        "STORAGE_TOKEN": "REV-STORAGE",
        "GRAPH_TOKEN":   "REV-GRAPH",
        "COMPUTE_TOKEN": "REV-COMPUTE",
        "API_TOKEN":     "REV-API",
    }
    code = code_map.get(asset_unit_name)
    if not code:
        return None
    return LedgerAccount.objects.filter(code=code).first()


class Command(IngressCommand):
    help = "Seed community treasury accounts and the base community-plan tariff."

    def process(self):
        self._ensure_all_treasury_accounts()
        self._ensure_community_tariff()
        if self.full:
            self._fund_admin_user()

    # ------------------------------------------------------------------
    # 1. Treasury accounts for every community
    # ------------------------------------------------------------------

    def _ensure_all_treasury_accounts(self):
        from toto.socialhub.models import Community

        communities = Community.objects.all()
        created_count = 0
        for community in communities:
            _, created = get_or_create_treasury_account(community)
            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f"  + treasury account for '{community.name}'"))

        if created_count == 0:
            self.stdout.write("  Treasury accounts: all communities already have accounts.")
        else:
            self.stdout.write(self.style.SUCCESS(f"  Created {created_count} treasury account(s)."))

    # ------------------------------------------------------------------
    # 2. Community-specific tariff for "Our Thing Inc."
    # ------------------------------------------------------------------

    def _ensure_community_tariff(self):
        from toto.assets.models import Asset
        from toto.socialhub.models import Community

        community = Community.objects.filter(name="Our Thing Inc.").first()
        if community is None:
            self.stdout.write(self.style.WARNING(
                "  Community 'Our Thing Inc.' not found — skipping community tariff. "
                "Run ingress_socialhub first."
            ))
            return

        treasury_account, _ = get_or_create_treasury_account(community)

        tariff, created = Tariff.objects.get_or_create(
            code=_COMMUNITY_TARIFF_CODE,
            defaults={
                "name": "Our Thing — Community Plan",
                "description": (
                    "Community-specific rate card for 'Our Thing Inc.' members. "
                    "Provides 20% discount across all metered services vs. platform defaults."
                ),
                "status": TariffStatus.ACTIVE,
                "source_type": "socialhub.Community",
                "source_id": str(community.pk),
            },
        )
        if not created:
            self.stdout.write(f"  Community tariff '{_COMMUNITY_TARIFF_CODE}' already exists.")
        else:
            self.stdout.write(self.style.SUCCESS(f"  + tariff '{_COMMUNITY_TARIFF_CODE}' for {community.name}"))

        # Ensure source linkage is correct even on existing tariff
        if tariff.source_type != "socialhub.Community" or tariff.source_id != str(community.pk):
            tariff.source_type = "socialhub.Community"
            tariff.source_id = str(community.pk)
            tariff.save(update_fields=["source_type", "source_id", "updated_at"])

        items_created = 0
        items_skipped = 0
        for metric_code, platform_price_str, asset_unit, uq in _COMMUNITY_ITEMS:
            metric = BillingMetric.objects.filter(code=metric_code).first()
            if metric is None:
                self.stdout.write(self.style.WARNING(
                    f"  Metric '{metric_code}' not found — run ingress_tariffs first."
                ))
                continue

            asset = Asset.objects.filter(unit_name=asset_unit).first()
            if asset is None:
                self.stdout.write(self.style.WARNING(
                    f"  Asset '{asset_unit}' not found — run ingress_tariffs first."
                ))
                continue

            recv = _get_rev_account(asset_unit)
            if recv is None:
                self.stdout.write(self.style.WARNING(
                    f"  Revenue account for '{asset_unit}' not found — skipping."
                ))
                continue

            # Apply 20% discount
            platform_price = Decimal(platform_price_str)
            community_price = (platform_price * _COMMUNITY_DISCOUNT).quantize(Decimal("0.0001"))
            price_base = to_base_units(community_price, asset.decimals)

            _, item_created = TariffItem.objects.get_or_create(
                tariff=tariff,
                metric=metric,
                defaults={
                    "name": f"{metric.label} (community rate)",
                    "charged_asset": asset,
                    "price_per_unit_display": community_price,
                    "price_per_unit_base_units": price_base,
                    "unit_quantity": Decimal(str(uq)),
                    "receiving_account": recv,
                    "rounding_mode": RoundingMode.UP,
                    "minimum_charge_base_units": 0,
                    "active": True,
                },
            )
            if item_created:
                items_created += 1
            else:
                items_skipped += 1

        self.stdout.write(self.style.SUCCESS(
            f"  Community tariff items: {items_created} created, {items_skipped} already existed."
        ))

    # ------------------------------------------------------------------
    # 3. Fund the admin user with test tokens
    # ------------------------------------------------------------------

    def _fund_admin_user(self):
        from toto.assets.models import Asset
        from toto.assets.prepaid import get_or_create_prepaid_account
        from toto.assets.services.assets import transfer_asset
        from toto.assets.queries import get_asset_balance_display
        import uuid as _uuid

        admin = User.objects.filter(username="admin").first()
        if admin is None:
            self.stdout.write(self.style.WARNING("  Admin user not found — skipping token funding."))
            return

        prepaid_account, _ = get_or_create_prepaid_account(admin)

        assets_to_fund = [
            "AI_TOKEN", "STORAGE_TOKEN", "GRAPH_TOKEN", "COMPUTE_TOKEN", "API_TOKEN",
        ]

        for unit_name in assets_to_fund:
            asset = Asset.objects.filter(unit_name=unit_name).first()
            if asset is None or not asset.reserve_account_id:
                self.stdout.write(self.style.WARNING(f"  Asset {unit_name} not ready — skip."))
                continue

            current = get_asset_balance_display(asset, prepaid_account)
            to_add = _ADMIN_FUND_AMOUNT - current
            if to_add <= 0:
                self.stdout.write(f"  Admin already has {current} {unit_name}.")
                continue

            ref = f"ingress-treasury-admin-{unit_name.lower()}-{_uuid.uuid4().hex[:6]}"
            try:
                transfer_asset(
                    asset=asset,
                    sender_account=asset.reserve_account,
                    receiver_account=prepaid_account,
                    amount=to_add,
                    reference=ref,
                    description=f"Treasury ingress: fund admin with {unit_name} for testing",
                )
                self.stdout.write(self.style.SUCCESS(
                    f"  + funded admin with {to_add} {unit_name} (total: {_ADMIN_FUND_AMOUNT})"
                ))
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"  Could not fund {unit_name}: {exc}"))

        self.stdout.write(self.style.SUCCESS("  Admin user funded for testing."))
