from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from toto.assets.models import (
    AccountType,
    Asset,
    Currency,
    LedgerAccount,
    LedgerTransaction,
)
from toto.assets.services.assets import (
    create_asset,
    create_obligation,
    reverse_transaction,
    transfer_asset,
)
from toto.ingress import IngressCommand

# ── Platform currency constants ────────────────────────────────────────────
# Supplies are frozen at their historical seed values so re-running ingress
# against an already-seeded ledger never trips the immutability re-check.
ASR_SUPPLY = Decimal("6666.666666667")

# The gas asset — what metered work is billed in — is per host. It was ASR
# everywhere until a second host started running its own economy (see the
# monorepo's studio.md): two ledgers that cannot exchange must not use one
# ticker, or a balance means different things depending where you read it.
# GAS_ASSET/GAS_SUPPLY/GAS_ASSET_NAME default to the historical Assarion, so a
# host that names none of them seeds exactly what it always did.
GAS_DEFAULTS = {
    "ASR": ("Assarion", "Assari",
            "Assarion — fine-grained unit of account of the platform. 9 decimal places."),
}
TPLN_SUPPLY = Decimal("76658.70")

assert ASR_SUPPLY == Decimal("6666.666666667"), ASR_SUPPLY
assert TPLN_SUPPLY == Decimal("76658.70"), TPLN_SUPPLY


class Command(IngressCommand):
    help = "Seed the asset ledger with the platform currencies and (optionally) demo assets."

    def process(self):
        self.stdout.write("🪙  Seeding assets ledger…")

        reserve_accounts = self._seed_reserve_accounts()
        currency_assets = self._seed_base_currency_assets(reserve_accounts)
        self._seed_base_currencies(currency_assets)

        if not self.full:
            # A real build ends here: the host's gas to spend, TPLN to account
            # in, nothing else. Every other asset below is demonstration material.
            gas_ticker = currency_assets["GAS"].unit_name
            self.stdout.write(self.style.SUCCESS(
                f"✅  Base currency ingress complete ({gas_ticker} + TPLN). "
                "Use --full for the demo assets and data."
            ))
            return

        # Everything below this point is full-only:
        # demo tokens, demo accounts, SPC/GGM/WUT, transfers, wallets,
        # agreements, founder obligations, contracts.
        demo_tokens = self._seed_demo_platform_assets()
        demo_accounts = self._seed_accounts()
        demo_assets = self._seed_assets(demo_accounts)
        contracts = self._seed_contracts()

        combined_assets = {**currency_assets, **demo_tokens, **demo_assets}
        self._seed_transfers(demo_assets, demo_accounts)
        self._seed_user_wallets(demo_accounts, combined_assets)
        self._seed_sample_agreements(demo_accounts, contracts)
        self._seed_founder_obligations(demo_accounts, combined_assets)

        self.stdout.write(self.style.SUCCESS("✅  Asset ledger ingress complete."))

    # ------------------------------------------------------------------ #
    # Always-on: Currency Reserve Account                                 #
    # ------------------------------------------------------------------ #

    def _seed_reserve_accounts(self) -> dict:
        acc, created = LedgerAccount.objects.get_or_create(
            code="currency-reserve",
            defaults={
                "name": "Currency Reserve",
                "account_type": AccountType.RESERVE,
                "active": True,
                "metadata": {
                    "kind": "currency_reserve",
                    "system": "assarion_tpln",
                },
            },
        )
        if not created:
            fields = []
            if acc.name != "Currency Reserve":
                acc.name = "Currency Reserve"
                fields.append("name")
            if acc.account_type != AccountType.RESERVE:
                acc.account_type = AccountType.RESERVE
                fields.append("account_type")
            if not acc.active:
                acc.active = True
                fields.append("active")
            meta = dict(acc.metadata or {})
            for k, v in {
                "kind": "currency_reserve",
                "system": "assarion_tpln",
            }.items():
                meta.setdefault(k, v)
            if meta != (acc.metadata or {}):
                acc.metadata = meta
                fields.append("metadata")
            if fields:
                acc.save(update_fields=fields)
        self.stdout.write("  +/✓ account currency-reserve")
        return {"currency_reserve": acc}

    # ------------------------------------------------------------------ #
    # Always-on: the host's gas asset, TPLN                              #
    # ------------------------------------------------------------------ #

    def _seed_base_currency_assets(self, accounts: dict) -> dict:
        reserve = accounts["currency_reserve"]
        assets = {}

        # ── gas (ASR unless this host names another) ─────────────────────
        ticker = getattr(settings, "GAS_ASSET", "ASR")
        supply = Decimal(str(getattr(settings, "GAS_SUPPLY", ASR_SUPPLY)))
        name, plural, description = GAS_DEFAULTS.get(
            ticker,
            (getattr(settings, "GAS_ASSET_NAME", ticker), ticker,
             f"{ticker} — what metered work on this platform is billed in."),
        )
        # One reference per ticker: create_asset is one-shot per reference, and
        # that is the whole immutability guarantee for a fixed supply.
        reference = f"create-{ticker.lower()}"

        if LedgerTransaction.objects.filter(reference=reference).exists():
            gas = Asset.objects.get(unit_name=ticker)
            existing_supply = gas.total_supply_display
            if existing_supply != supply:
                self.stdout.write(self.style.WARNING(
                    f"  ⚠ {ticker} already exists with supply={existing_supply}; "
                    f"expected {supply}. Ledger immutability preserved — "
                    "update total_supply manually via a formal correction/reversal if needed."
                ))
        else:
            gas = create_asset(
                name=name,
                unit_name=ticker,
                total_supply=supply,
                decimals=9,
                reserve_account=reserve,
                reference=reference,
                description=description,
                metadata={
                    "kind": "currency",
                    "family": "toto_currency",
                    "plural": plural,
                    "seeded_by": "ingress",
                },
            )
            gas.reserve_account = reserve
            gas.save(update_fields=["reserve_account", "updated_at"])

        gas.is_currency = True
        gas.backing_document = "Issued and held by the platform's Currency Reserve account."
        gas.minting_authority = "Currency Reserve"
        gas.save(update_fields=["is_currency", "backing_document", "minting_authority", "updated_at"])
        assets[ticker] = gas
        assets["GAS"] = gas          # ticker-agnostic handle for the seeder below
        self.stdout.write(f"  +/✓ asset {ticker} (gas) supply={supply}")

        # ── TPLN ─────────────────────────────────────────────────────────
        if LedgerTransaction.objects.filter(reference="create-tpln").exists():
            tpln = Asset.objects.get(unit_name="TPLN")
            existing_supply = tpln.total_supply_display
            if existing_supply != TPLN_SUPPLY:
                self.stdout.write(self.style.WARNING(
                    f"  ⚠ TPLN already exists with supply={existing_supply}; "
                    f"expected {TPLN_SUPPLY}. Ledger immutability preserved — "
                    "update total_supply manually via a formal correction/reversal if needed."
                ))
        else:
            tpln = create_asset(
                name="Toto Złoty",
                unit_name="TPLN",
                total_supply=TPLN_SUPPLY,
                decimals=2,
                reserve_account=reserve,
                reference="create-tpln",
                description="Internal accounting currency of the platform. 2 decimal places.",
                metadata={
                    "kind": "currency",
                    "family": "toto_currency",
                    "seeded_by": "ingress",
                },
            )
            tpln.reserve_account = reserve
            tpln.save(update_fields=["reserve_account", "updated_at"])

        tpln.is_currency = True
        tpln.backing_document = "Issued and held by the platform's Currency Reserve account."
        tpln.minting_authority = "Currency Reserve"
        tpln.save(update_fields=["is_currency", "backing_document", "minting_authority", "updated_at"])
        assets["TPLN"] = tpln
        self.stdout.write(f"  +/✓ asset TPLN supply={TPLN_SUPPLY}")

        assert Asset.objects.filter(unit_name=ticker).exists()
        assert Asset.objects.filter(unit_name="TPLN").exists()
        assert gas.decimals == 9
        assert tpln.decimals == 2
        assert gas.is_currency is True
        assert tpln.is_currency is True

        return assets

    # ------------------------------------------------------------------ #
    # Always-on: Currencies for the gas asset and TPLN                   #
    # ------------------------------------------------------------------ #

    def _seed_base_currencies(self, assets: dict) -> dict:
        gas = assets["GAS"]
        specs = [
            dict(code=gas.unit_name, name=gas.name, symbol=gas.unit_name,
                 unit_name=gas.unit_name),
            dict(code="TPLN", name="Toto Złoty", symbol="tzł", unit_name="TPLN"),
        ]
        currencies = {}
        for spec in specs:
            unit = spec.pop("unit_name")
            asset = assets[unit]
            cur, created = Currency.objects.update_or_create(
                code=spec["code"],
                defaults={**spec, "asset": asset, "is_active": True},
            )
            currencies[spec["code"]] = cur
            self.stdout.write(f"  +/✓ currency {spec['code']}")

        gas_cur = Currency.objects.select_related("asset").get(code=gas.unit_name)
        tpln_cur = Currency.objects.select_related("asset").get(code="TPLN")
        assert gas_cur.asset_id == gas.pk
        assert tpln_cur.asset_id == assets["TPLN"].pk

        return currencies

    # ------------------------------------------------------------------ #
    # Full-only: demo tokens (BANANA, MAKARONI)                          #
    # ------------------------------------------------------------------ #

    def _seed_demo_platform_assets(self) -> dict:
        """Playful per-resource tokens, for demos only.

        A real build has exactly two assets — ASR to spend and TPLN to account
        in — so these stay out of it. They exist to show that a price row can
        name any asset, not just the gas.
        """
        from django.contrib.auth import get_user_model
        User = get_user_model()
        admin = User.objects.filter(is_superuser=True).order_by("id").first()

        platform_reserve, created = LedgerAccount.objects.get_or_create(
            code="platform-reserve",
            defaults={
                "name": "Platform Reserve",
                "account_type": AccountType.RESERVE,
                "active": True,
                "user": admin,
                "metadata": {"kind": "platform_token_reserve"},
            },
        )
        if not created and admin and platform_reserve.user_id != admin.pk:
            platform_reserve.user = admin
            platform_reserve.save(update_fields=["user"])
        self.stdout.write("  +/✓ account platform-reserve" + (f" (linked to {admin})" if admin else ""))

        assets = {}
        specs = [
            dict(
                name="Banana Token",
                unit_name="BANANA",
                total_supply=Decimal("1000000000.000"),
                decimals=3,
                reserve_account=platform_reserve,
                reference="create-banana",
                description=(
                    "AI inference token. 1 BANANA = 1 banana (the fruit, ~120 g, ~0.42 PLN). "
                    "Priced at OpenAI GPT-4o rates converted to PLN (3.95 USD/PLN) with 270% margin."
                ),
                metadata={
                    "kind": "platform_token",
                    "unit": "banana",
                    "unit_label": "1 token = 1 banana (~120 g, ~0.42 PLN)",
                    "pricing_reference": (
                        "OpenAI GPT-4o × 2.70 margin, converted via 3.95 PLN/USD, "
                        "then ÷ 0.42 PLN/banana"
                    ),
                    "banana_price_pln": "0.42",
                    "usd_pln_rate": "3.95",
                    "margin": "2.70",
                    "seeded_by": "ingress",
                },
            ),
            dict(
                name="Makaroni Token",
                unit_name="MAKARONI",
                total_supply=Decimal("1000000000"),
                decimals=0,
                reserve_account=platform_reserve,
                reference="create-makaroni",
                description=(
                    "Graph query token. 1 MAKARONI = 1 dry macaroni piece (~0.5 g). "
                    "A 500 g bag ≈ 1,000 MAKARONI."
                ),
                metadata={
                    "kind": "platform_token",
                    "unit": "dry_macaroni_piece",
                    "unit_label": "1 token = 1 dry macaroni piece (~0.5 g)",
                    "bag_500g": "1000 MAKARONI",
                    "seeded_by": "ingress",
                },
            ),
        ]
        for spec in specs:
            ref = spec["reference"]
            if LedgerTransaction.objects.filter(reference=ref).exists():
                asset = Asset.objects.get(unit_name=spec["unit_name"])
                self.stdout.write(self.style.WARNING(f"  ⚠ skipped existing asset {spec['unit_name']}"))
            else:
                try:
                    asset = create_asset(**spec)
                    asset.reserve_account = platform_reserve
                    asset.save(update_fields=["reserve_account", "updated_at"])
                    self.stdout.write(f"  +/✓ asset {spec['unit_name']}  supply={spec['total_supply']}")
                except Exception as exc:
                    self.stdout.write(self.style.ERROR(f"  ✗ {spec['unit_name']}: {exc}"))
                    continue
            assets[spec["unit_name"]] = asset
        return assets

    # ------------------------------------------------------------------ #
    # Full-only: Demo Accounts                                            #
    # ------------------------------------------------------------------ #

    def _seed_accounts(self) -> dict:
        specs = [
            ("reserve_main",  "Main Reserve",   "reserve"),
            ("reserve_spc",   "SPC Reserve",    "reserve"),
            ("treasury",      "Treasury",       "reserve"),
            ("alice",         "Alice Vance",    "user"),
            ("bob",           "Bob Kiran",      "user"),
            ("carol",         "Carol Osei",     "user"),
            ("david",         "David Tomasz",   "user"),
            ("market_maker",  "Market Maker",   "external"),
            ("ops",           "Operations",     "system"),
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
    # Full-only: Demo Assets (SPC, GGM, WUT)                             #
    # ------------------------------------------------------------------ #

    def _seed_assets(self, accounts: dict) -> dict:
        specs = [
            dict(
                name="Spectrum Credit",
                unit_name="SPC",
                total_supply=Decimal("1000000"),
                decimals=2,
                reserve_account=accounts["reserve_spc"],
                reference="create-spc",
                description="Primary platform credit token. 2 decimal places.",
            ),
            dict(
                name="Gold Gram",
                unit_name="GGM",
                total_supply=Decimal("50000.000"),
                decimals=3,
                reserve_account=accounts["reserve_main"],
                reference="create-ggm",
                description="Commodity token referencing one gram of gold.",
            ),
            dict(
                name="Whole Unit Token",
                unit_name="WUT",
                total_supply=Decimal("10000"),
                decimals=0,
                reserve_account=accounts["treasury"],
                reference="create-wut",
                description="Indivisible governance token. No sub-units.",
                metadata={"governance": True, "voting_weight": 1},
            ),
        ]
        assets = {}
        for spec in specs:
            ref = spec["reference"]
            if LedgerTransaction.objects.filter(reference=ref).exists():
                asset = Asset.objects.get(unit_name=spec["unit_name"])
                assets[spec["unit_name"]] = asset
                self.stdout.write(self.style.WARNING(f"  ⚠ skipped existing asset {spec['unit_name']}"))
                continue
            try:
                asset = create_asset(**spec)
                assets[spec["unit_name"]] = asset
                self.stdout.write(f"  + asset {spec['unit_name']}  supply={spec['total_supply']}")
            except (ValidationError, Exception) as exc:
                self.stdout.write(self.style.ERROR(f"  ✗ {spec['unit_name']}: {exc}"))
        return assets

    # ------------------------------------------------------------------ #
    # Full-only: Transfers                                                #
    # ------------------------------------------------------------------ #

    def _seed_transfers(self, assets: dict, accounts: dict):
        spc = assets.get("SPC")
        ggm = assets.get("GGM")
        wut = assets.get("WUT")

        transfers = []

        if spc:
            transfers += [
                dict(asset=spc, sender_account=accounts["reserve_spc"],
                     receiver_account=accounts["alice"],
                     amount=Decimal("25000.00"),
                     reference="txfr-spc-reserve-alice-001",
                     description="Initial allocation to Alice"),
                dict(asset=spc, sender_account=accounts["reserve_spc"],
                     receiver_account=accounts["bob"],
                     amount=Decimal("15000.00"),
                     reference="txfr-spc-reserve-bob-001",
                     description="Initial allocation to Bob"),
                dict(asset=spc, sender_account=accounts["reserve_spc"],
                     receiver_account=accounts["market_maker"],
                     amount=Decimal("100000.00"),
                     reference="txfr-spc-reserve-mm-001",
                     description="Liquidity provision to market maker"),
                dict(asset=spc, sender_account=accounts["alice"],
                     receiver_account=accounts["carol"],
                     amount=Decimal("5000.00"),
                     reference="txfr-spc-alice-carol-001",
                     description="Alice sends SPC to Carol"),
                dict(asset=spc, sender_account=accounts["bob"],
                     receiver_account=accounts["david"],
                     amount=Decimal("2500.00"),
                     reference="txfr-spc-bob-david-001",
                     description="Bob sends SPC to David"),
                dict(asset=spc, sender_account=accounts["market_maker"],
                     receiver_account=accounts["carol"],
                     amount=Decimal("1000.00"),
                     reference="txfr-spc-mm-carol-001",
                     description="Mistaken transfer — will be reversed"),
            ]

        if ggm:
            transfers += [
                dict(asset=ggm, sender_account=accounts["reserve_main"],
                     receiver_account=accounts["alice"],
                     amount=Decimal("100.000"),
                     reference="txfr-ggm-reserve-alice-001",
                     description="Gold allocation to Alice"),
                dict(asset=ggm, sender_account=accounts["reserve_main"],
                     receiver_account=accounts["market_maker"],
                     amount=Decimal("5000.000"),
                     reference="txfr-ggm-reserve-mm-001",
                     description="Gold liquidity to market maker"),
                dict(asset=ggm, sender_account=accounts["alice"],
                     receiver_account=accounts["bob"],
                     amount=Decimal("25.500"),
                     reference="txfr-ggm-alice-bob-001",
                     description="Alice sells gold to Bob"),
            ]

        if wut:
            transfers += [
                dict(asset=wut, sender_account=accounts["treasury"],
                     receiver_account=accounts["alice"],
                     amount=Decimal("200"),
                     reference="txfr-wut-treasury-alice-001",
                     description="Governance tokens to Alice"),
                dict(asset=wut, sender_account=accounts["treasury"],
                     receiver_account=accounts["bob"],
                     amount=Decimal("150"),
                     reference="txfr-wut-treasury-bob-001",
                     description="Governance tokens to Bob"),
                dict(asset=wut, sender_account=accounts["treasury"],
                     receiver_account=accounts["market_maker"],
                     amount=Decimal("500"),
                     reference="txfr-wut-treasury-mm-001",
                     description="Governance tokens to market maker"),
            ]

        reversal_refs = {"txfr-spc-mm-carol-001"}
        executed = {}

        for spec in transfers:
            ref = spec["reference"]
            if LedgerTransaction.objects.filter(reference=ref).exists():
                tx = LedgerTransaction.objects.get(reference=ref)
                executed[ref] = tx
                self.stdout.write(self.style.WARNING(f"  ⚠ skipped existing transfer {ref}"))
                continue
            try:
                tx = transfer_asset(**spec)
                executed[ref] = tx
                self.stdout.write(f"  + transfer {ref}")
            except (ValidationError, Exception) as exc:
                self.stdout.write(self.style.ERROR(f"  ✗ {ref}: {exc}"))

        for ref in reversal_refs:
            rev_ref = f"rev-{ref}"
            if LedgerTransaction.objects.filter(reference=rev_ref).exists():
                self.stdout.write(self.style.WARNING(f"  ⚠ skipped existing reversal {rev_ref}"))
                continue
            tx = executed.get(ref)
            if not tx:
                continue
            try:
                reverse_transaction(
                    transaction=tx,
                    reference=rev_ref,
                    description=f"Reversing mistaken transfer: {ref}",
                )
                self.stdout.write(f"  + reversal {rev_ref}")
            except (ValidationError, Exception) as exc:
                self.stdout.write(self.style.ERROR(f"  ✗ {rev_ref}: {exc}"))

    # ------------------------------------------------------------------ #
    # Full-only: User wallet funding                                      #
    # ------------------------------------------------------------------ #

    def _seed_user_wallets(self, accounts: dict, assets: dict):
        tpln = assets.get("TPLN")

        distributions = []
        if tpln and tpln.reserve_account:
            distributions += [
                dict(asset=tpln, sender_account=tpln.reserve_account,
                     receiver_account=accounts["alice"],
                     amount=Decimal("5000.00"), reference="dist-tpln-alice-001",
                     description="TPLN allocation to Alice"),
                dict(asset=tpln, sender_account=tpln.reserve_account,
                     receiver_account=accounts["bob"],
                     amount=Decimal("3000.00"), reference="dist-tpln-bob-001",
                     description="TPLN allocation to Bob"),
                dict(asset=tpln, sender_account=tpln.reserve_account,
                     receiver_account=accounts["carol"],
                     amount=Decimal("2000.00"), reference="dist-tpln-carol-001",
                     description="TPLN allocation to Carol"),
            ]

        for spec in distributions:
            ref = spec["reference"]
            if LedgerTransaction.objects.filter(reference=ref).exists():
                self.stdout.write(self.style.WARNING(f"  ⚠ skipped existing distribution {ref}"))
                continue
            try:
                transfer_asset(**spec)
                self.stdout.write(f"  + distribution {ref}")
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  ✗ {ref}: {exc}"))

    # ------------------------------------------------------------------ #
    # Full-only: Contracts                                                #
    # ------------------------------------------------------------------ #

    _CONTRACT_SPECS = [
        dict(
            name="Noop",
            code=(
                "language: lapis\n"
                "version: 1\n"
                "name: Noop\n"
                "actions:\n"
                "  execute:\n"
                "    body:\n"
                "      type: seq\n"
                "      steps:\n"
                "        - type: approve\n"
            ),
            metadata={"description": "Does nothing — useful for testing wiring."},
        ),
        dict(
            name="Log Event",
            code=(
                "language: lapis\n"
                "version: 1\n"
                "name: LogEvent\n"
                "actions:\n"
                "  execute:\n"
                "    body:\n"
                "      type: seq\n"
                "      steps:\n"
                "        - type: log\n"
                "          value:\n"
                "            type: bytes\n"
                "            value: agreement_executed\n"
                "        - type: approve\n"
            ),
            metadata={"description": "Logs an agreement_executed message on every execution."},
        ),
        dict(
            name="Assert State",
            code=(
                "language: lapis\n"
                "version: 1\n"
                "name: AssertState\n"
                "actions:\n"
                "  execute:\n"
                "    body:\n"
                "      type: seq\n"
                "      steps:\n"
                "        - type: assert\n"
                "          condition:\n"
                "            type: eq\n"
                "            left:\n"
                "              type: app_global_get\n"
                "              key:\n"
                "                type: bytes\n"
                "                value: status\n"
                "            right:\n"
                "              type: bytes\n"
                "              value: active\n"
                "        - type: log\n"
                "          value:\n"
                "            type: bytes\n"
                "            value: state_checked\n"
                "        - type: approve\n"
            ),
            metadata={"description": "Asserts global state 'status' equals 'active', then logs and approves."},
        ),
    ]

    def _seed_contracts(self) -> dict:
        from toto.assets.models import Contract

        contracts = {}
        for spec in self._CONTRACT_SPECS:
            name = spec["name"]
            contract, created = Contract.objects.get_or_create(
                name=name,
                defaults={"code": spec["code"], "metadata": spec.get("metadata", {})},
            )
            contracts[name] = contract
            if created:
                self.stdout.write(f"  + contract '{name}'")
            else:
                self.stdout.write(self.style.WARNING(f"  ⚠ skipped existing contract '{name}'"))
        return contracts

    def _seed_sample_agreements(self, accounts: dict, contracts: dict):
        from toto.assets.models import Agreement

        noop = contracts.get("Noop")
        record = contracts.get("Log Event")

        specs = []
        if noop and "alice" in accounts and "bob" in accounts:
            specs.append(dict(
                source_account=accounts["alice"],
                target_account=accounts["bob"],
                contract=noop,
                metadata={"note": "Demo noop agreement between Alice and Bob."},
            ))
        if record and "bob" in accounts and "carol" in accounts:
            specs.append(dict(
                source_account=accounts["bob"],
                target_account=accounts["carol"],
                contract=record,
                metadata={"note": "Demo record-event agreement between Bob and Carol."},
            ))

        for spec in specs:
            src = spec["source_account"].code
            tgt = spec["target_account"].code
            if Agreement.objects.filter(
                source_account=spec["source_account"],
                target_account=spec["target_account"],
            ).exists():
                self.stdout.write(self.style.WARNING(f"  ⚠ skipped existing agreement {src}→{tgt}"))
                continue
            try:
                Agreement.objects.create(**spec)
                self.stdout.write(f"  + agreement {src}→{tgt}")
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  ✗ agreement {src}→{tgt}: {exc}"))

    # ------------------------------------------------------------------ #
    # Full-only: Founder obligations                                      #
    # ------------------------------------------------------------------ #

    def _seed_founder_obligations(self, accounts: dict, assets: dict):
        from django.contrib.auth import get_user_model
        from toto.assets.models import Obligation

        User = get_user_model()
        admin = User.objects.filter(is_superuser=True).order_by("id").first()
        if not admin:
            self.stdout.write(self.style.WARNING("  ⚠ No superuser found — skipping founder obligations."))
            return

        founder_account, created = LedgerAccount.objects.get_or_create(
            code="founder",
            defaults={
                "name": f"Founder — {admin.get_full_name() or admin.username}",
                "account_type": "user",
                "active": True,
                "user": admin,
            },
        )
        if not created and founder_account.user_id != admin.pk:
            founder_account.user = admin
            founder_account.save(update_fields=["user"])
        if created:
            self.stdout.write(f"  + account founder (linked to {admin})")

        treasury = accounts.get("treasury")
        tpln = assets.get("TPLN")

        funding = []
        if tpln and tpln.reserve_account:
            funding.append(dict(
                asset=tpln, sender_account=tpln.reserve_account,
                receiver_account=founder_account,
                amount=Decimal("8000.00"),
                reference="dist-tpln-founder-001",
                description="TPLN allocation to Founder",
            ))

        for spec in funding:
            ref = spec["reference"]
            if LedgerTransaction.objects.filter(reference=ref).exists():
                self.stdout.write(self.style.WARNING(f"  ⚠ skipped existing funding {ref}"))
                continue
            try:
                transfer_asset(**spec)
                self.stdout.write(f"  + funding {ref}")
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  ✗ {ref}: {exc}"))

        obligation_specs = []
        if tpln and treasury:
            obligation_specs.append(dict(
                reference="test-obligation-founder-tpln-001",
                debtor_account=founder_account,
                creditor_account=treasury,
                asset=tpln,
                amount=Decimal("250.00"),
                due_at=timezone.now() + timedelta(days=14),
                order_reference="test-fine-001",
            ))
            obligation_specs.append(dict(
                reference="test-obligation-founder-tpln-overdue-001",
                debtor_account=founder_account,
                creditor_account=treasury,
                asset=tpln,
                amount=Decimal("75.00"),
                due_at=timezone.now() - timedelta(days=3),
                order_reference="test-fine-003",
            ))

        for spec in obligation_specs:
            ref = spec["reference"]
            if Obligation.objects.filter(reference=ref).exists():
                self.stdout.write(self.style.WARNING(f"  ⚠ skipped existing obligation {ref}"))
                continue
            try:
                create_obligation(**spec)
                self.stdout.write(f"  + obligation {ref}")
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  ✗ {ref}: {exc}"))
