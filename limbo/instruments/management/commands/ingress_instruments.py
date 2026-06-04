from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from toto.ingress import IngressCommand
from toto.instruments.models import (
    AmortizationContract,
    EscrowContract,
    FinancialInstrument,
    ForwardContract,
    OptionContract,
    RevenueShareContract,
    RevenueShareRecipient,
    StakingPosition,
    VestingContract,
)

_DEMO_TYPES = [
    "amortization",
    "vesting",
    "escrow",
    "forward",
    "option",
    "staking",
    "revenue_share",
]


class Command(IngressCommand):
    help = "Seed demo financial instruments and optionally deploy Lapis Contracts."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            "--deploy-contracts",
            action="store_true",
            help="Generate and deploy a Lapis Contract for each demo instrument.",
        )
        parser.add_argument(
            "--force-contracts",
            action="store_true",
            help="Implies --deploy-contracts; overwrites existing Contract code and metadata.",
        )
        parser.add_argument(
            "--sync-lifecycle",
            action="store_true",
            help="Create/sync lifecycle primitives (Schedule, Condition, Allocation, Entitlement, ContractEvent) for each demo instrument.",
        )

    def handle(self, *args, **options):
        self.force_contracts = options.get("force_contracts", False)
        self.deploy_contracts = (
            options.get("deploy_contracts", False) or self.force_contracts
        )
        self.sync_lifecycle = options.get("sync_lifecycle", False)
        super().handle(*args, **options)

    def process(self):
        if not self.full:
            return
        self.stdout.write("📄  Seeding demo financial instruments…")
        accounts = self._seed_accounts()
        assets = self._seed_assets(accounts)
        instruments = self._seed_instruments(accounts, assets)

        if self.deploy_contracts:
            self._deploy_contracts(instruments)

        if self.sync_lifecycle:
            self._sync_lifecycles(instruments)

        self.stdout.write(self.style.SUCCESS("✅  Instruments ingress complete."))

    # ------------------------------------------------------------------ #
    # Accounts                                                             #
    # ------------------------------------------------------------------ #

    def _seed_accounts(self) -> dict:
        from toto.assets.models import LedgerAccount

        specs = [
            ("instr-alice",   "Instruments Alice",        "user"),
            ("instr-bob",     "Instruments Bob",          "user"),
            ("instr-carol",   "Instruments Carol",        "user"),
            ("instr-vault",   "Instruments Escrow Vault", "system"),
            ("instr-revenue", "Instruments Revenue",      "system"),
            ("instr-staking", "Instruments Staking Pool", "system"),
            ("instr-reserve", "Instruments Reserve",      "reserve"),
        ]
        accounts = {}
        for code, name, atype in specs:
            acc, created = LedgerAccount.objects.get_or_create(
                code=code,
                defaults={"name": name, "account_type": atype, "active": True},
            )
            accounts[code] = acc
            if created:
                self.stdout.write(f"  + account {code}")
        return accounts

    # ------------------------------------------------------------------ #
    # Assets                                                               #
    # ------------------------------------------------------------------ #

    def _get_or_create_asset(self, unit_name: str, reserve_account):
        from toto.assets.models import Asset, LedgerTransaction
        from toto.assets.services.assets import create_asset

        ref = f"create-instr-{unit_name.lower()}"
        if LedgerTransaction.objects.filter(reference=ref).exists():
            return Asset.objects.get(unit_name=unit_name)
        asset = create_asset(
            name=unit_name,
            unit_name=unit_name,
            total_supply=Decimal("1000000"),
            decimals=2,
            reserve_account=reserve_account,
            reference=ref,
        )
        self.stdout.write(f"  + asset {unit_name}")
        return asset

    def _seed_assets(self, accounts: dict) -> dict:
        reserve = accounts["instr-reserve"]
        return {
            "IDEMO": self._get_or_create_asset("IDEMO", reserve),
            "IPAY":  self._get_or_create_asset("IPAY", reserve),
        }

    # ------------------------------------------------------------------ #
    # Instruments                                                          #
    # ------------------------------------------------------------------ #

    def _seed_instruments(self, accounts: dict, assets: dict) -> dict:
        now = timezone.now()
        idemo = assets["IDEMO"]
        ipay  = assets["IPAY"]
        alice   = accounts["instr-alice"]
        bob     = accounts["instr-bob"]
        vault   = accounts["instr-vault"]
        revenue = accounts["instr-revenue"]
        staking = accounts["instr-staking"]

        instruments = {}

        # ── amortization ─────────────────────────────────────────────────
        instr, _ = FinancialInstrument.objects.get_or_create(
            reference="demo-amortization-001",
            defaults={"instrument_type": "amortization"},
        )
        AmortizationContract.objects.get_or_create(
            instrument=instr,
            defaults=dict(
                source_account=alice,
                destination_account=bob,
                asset=idemo,
                original_amount_base_units=10000,
                basis="straight-line",
                starts_at=now,
                ends_at=now + timedelta(days=365),
            ),
        )
        instruments["amortization"] = instr

        # ── vesting ──────────────────────────────────────────────────────
        instr, _ = FinancialInstrument.objects.get_or_create(
            reference="demo-vesting-001",
            defaults={"instrument_type": "vesting"},
        )
        VestingContract.objects.get_or_create(
            instrument=instr,
            defaults=dict(
                grantor_account=bob,
                beneficiary_account=alice,
                asset=idemo,
                total_amount_base_units=24000,
                start_at=now,
                cliff_at=now + timedelta(days=90),
                end_at=now + timedelta(days=730),
                release_frequency="monthly",
            ),
        )
        instruments["vesting"] = instr

        # ── escrow ───────────────────────────────────────────────────────
        instr, _ = FinancialInstrument.objects.get_or_create(
            reference="demo-escrow-001",
            defaults={"instrument_type": "escrow"},
        )
        EscrowContract.objects.get_or_create(
            instrument=instr,
            defaults=dict(
                buyer_account=alice,
                seller_account=bob,
                escrow_account=vault,
                asset=idemo,
                amount_base_units=5000,
            ),
        )
        instruments["escrow"] = instr

        # ── forward ──────────────────────────────────────────────────────
        instr, _ = FinancialInstrument.objects.get_or_create(
            reference="demo-forward-001",
            defaults={"instrument_type": "forward"},
        )
        ForwardContract.objects.get_or_create(
            instrument=instr,
            defaults=dict(
                buyer_account=alice,
                seller_account=bob,
                underlying_asset=idemo,
                quantity_base_units=100,
                payment_asset=ipay,
                payment_amount_base_units=20000,
                settlement_at=now + timedelta(days=90),
            ),
        )
        instruments["forward"] = instr

        # ── option ───────────────────────────────────────────────────────
        instr, _ = FinancialInstrument.objects.get_or_create(
            reference="demo-option-001",
            defaults={"instrument_type": "option"},
        )
        OptionContract.objects.get_or_create(
            instrument=instr,
            defaults=dict(
                option_type="call",
                buyer_account=alice,
                writer_account=bob,
                underlying_asset=idemo,
                quantity_base_units=50,
                payment_asset=ipay,
                strike_price_base_units=10000,
                premium_base_units=500,
                expiry_at=now + timedelta(days=60),
            ),
        )
        instruments["option"] = instr

        # ── staking ──────────────────────────────────────────────────────
        instr, _ = FinancialInstrument.objects.get_or_create(
            reference="demo-staking-001",
            defaults={"instrument_type": "staking"},
        )
        StakingPosition.objects.get_or_create(
            instrument=instr,
            defaults=dict(
                staker_account=alice,
                staking_account=staking,
                staked_asset=idemo,
                staked_amount_base_units=2000,
                reward_asset=ipay,
                reward_rate_bps=500,
            ),
        )
        instruments["staking"] = instr

        # ── revenue_share ────────────────────────────────────────────────
        instr, _ = FinancialInstrument.objects.get_or_create(
            reference="demo-revenue-share-001",
            defaults={"instrument_type": "revenue_share"},
        )
        rs, _ = RevenueShareContract.objects.get_or_create(
            instrument=instr,
            defaults=dict(
                revenue_account=revenue,
                revenue_asset=idemo,
                active_from=now,
            ),
        )
        RevenueShareRecipient.objects.get_or_create(
            contract=rs, account=alice, defaults={"share_bps": 5000},
        )
        RevenueShareRecipient.objects.get_or_create(
            contract=rs, account=bob, defaults={"share_bps": 5000},
        )
        instruments["revenue_share"] = instr

        return instruments

    # ------------------------------------------------------------------ #
    # Contract deployment                                                   #
    # ------------------------------------------------------------------ #

    def _deploy_contracts(self, instruments: dict):
        from toto.instruments.lapis_factory import deploy_contract_for_instrument

        force = self.force_contracts
        ok = failed = 0

        for itype, instrument in instruments.items():
            try:
                instrument.refresh_from_db()
                had_contract = bool(instrument.contract_id)
                contract = deploy_contract_for_instrument(instrument, force=force)
                if force and had_contract:
                    verb = "force-updated"
                elif had_contract:
                    verb = "unchanged"
                else:
                    verb = "created"
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ {instrument.reference} [{itype}]"
                        f" → Contract {contract.pk} '{contract.name}' ({verb})"
                    )
                )
                ok += 1
            except Exception as exc:
                self.stdout.write(
                    self.style.WARNING(
                        f"  ⚠ {itype} ({instrument.reference}): cannot deploy — {exc}"
                    )
                )
                failed += 1

        suffix = " --force" if force else ""
        self.stdout.write(f"\nContracts{suffix}: {ok} deployed, {failed} failed.")

    # ------------------------------------------------------------------ #
    # Lifecycle sync                                                        #
    # ------------------------------------------------------------------ #

    def _sync_lifecycles(self, instruments: dict):
        from toto.instruments.lifecycle import sync_lifecycle_for_instrument

        self.stdout.write("\n🔄  Syncing lifecycle primitives…")
        totals = {"entitlements": 0, "schedules": 0, "conditions": 0, "allocations": 0, "events": 0}
        ok = failed = 0

        for itype, instrument in instruments.items():
            try:
                instrument.refresh_from_db()
                summary = sync_lifecycle_for_instrument(instrument)
                for key in totals:
                    totals[key] += summary.get(key, 0)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ {instrument.reference} [{itype}] → {summary}"
                    )
                )
                ok += 1
            except Exception as exc:
                self.stdout.write(
                    self.style.WARNING(
                        f"  ⚠ {itype} ({instrument.reference}): lifecycle sync failed — {exc}"
                    )
                )
                failed += 1

        self.stdout.write(
            f"\nLifecycle: {ok} synced, {failed} failed. "
            f"Primitives — entitlements: {totals['entitlements']}, "
            f"schedules: {totals['schedules']}, "
            f"conditions: {totals['conditions']}, "
            f"allocations: {totals['allocations']}."
        )
