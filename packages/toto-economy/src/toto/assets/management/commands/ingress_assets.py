from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from toto.assets.models import (
    AccountType,
    Asset,
    LedgerAccount,
    LedgerTransaction,
)
from toto.assets.services.assets import (
    reverse_transaction,
    transfer_asset,
)
from toto.ingress import IngressCommand
from toto.mint.services import create_currency

# ── Platform currency constants ────────────────────────────────────────────
# Supplies are frozen at their historical seed values so re-running ingress
# against an already-seeded ledger never trips the immutability re-check.
ASR_SUPPLY = Decimal("6666.666666667")

# The gas asset — what metered work is billed in — is no longer a per-host
# choice. Zenobia issues every currency and assigns each platform the one it
# bills in, so GAS_ASSET survives only as a SEED HINT here at genesis time:
# it names what to create on a master that has nothing yet. Nothing reads it at
# runtime any more — billing resolves through the currency contract. A branch
# seeds no gas at all; it mirrors what the master issued.
# See portal/hierarchical_economy.md.
GAS_DEFAULTS = {
    "ASR": ("Assarion", "Assari",
            "Assarion — fine-grained unit of account of the platform. 9 decimal places."),
}
#: The four-letter short name and the display symbol of a known gas ticker.
#: The short name keys the asset's page (/assets/assets/ASAR/); an unknown
#: ticker gets a code derived from its letters (Asset.save) and its ticker as
#: the symbol.
GAS_DISPLAY = {"ASR": ("ASAR", "ASR")}

# The Florin — the platform's accounting currency, named after the historic
# gold florin; no real currency stands behind it (economy.md, "No real
# currency, anywhere"). It replaced TPLN, the "Toto Złoty", on 2026-09-30.
# Its reference is create-flor, the same key bootstrap's CoreAsset derives from
# the ticker, so ingress and bootstrap can never mint two opening supplies.
FLOR_SUPPLY = Decimal("76658.70")
FLOR_CODE = "FLOR"
FLOR_SYMBOL = "ƒ"
FLOR_REFERENCE = "create-flor"

#: The reserve account's "system" tag.
RESERVE_SYSTEM = "assarion_florin"

assert ASR_SUPPLY == Decimal("6666.666666667"), ASR_SUPPLY
assert FLOR_SUPPLY == Decimal("76658.70"), FLOR_SUPPLY


class Command(IngressCommand):
    help = "Seed the asset ledger with the platform currencies and (optionally) demo assets."

    def process(self):
        self.stdout.write("🪙  Seeding assets ledger…")

        reserve_accounts = self._seed_reserve_accounts()

        # A BRANCH MIRRORS; IT DOES NOT SEED. Everything below engraves a
        # currency, and engraving calls require_master() — so on a host with no
        # issuer keypair this raised NotTheMaster out of the middle of a deploy,
        # after the reserve account had already been written. The ledger itself
        # is legitimate on a branch (that is where its allocated balance lives),
        # which is why the accounts above are still seeded; what a branch must
        # never do is invent money. Its currency arrives with a contract:
        #   manage.py export_contract          # on the master
        #   manage.py import_currency_contract # here
        # See portal/hierarchical_economy.md, "How an asset reaches a vassal".
        from toto.assets.issuer import is_monetary_master

        if not is_monetary_master():
            self.stdout.write(self.style.SUCCESS(
                "✅  Reserve accounts ready. No currency seeded: this host is "
                "not the monetary master, so it mirrors what the master issued "
                "rather than engraving its own. Import a currency contract to "
                "give it something to bill in."
            ))
            return

        currency_assets = self._seed_base_currency_assets(reserve_accounts)
        self._seed_base_currencies(currency_assets)

        if not self.full:
            # A real build ends here: the host's gas to spend, FLOR to account
            # in, nothing else. Every other asset below is demonstration material.
            gas_ticker = currency_assets["GAS"].unit_name
            self.stdout.write(self.style.SUCCESS(
                f"✅  Base currency ingress complete ({gas_ticker} + FLOR). "
                "Use --full for the demo assets and data."
            ))
            return

        # Everything below this point is full-only:
        # demo tokens, demo accounts, SPC/GGM/WUT, transfers, wallets.
        demo_tokens = self._seed_demo_platform_assets()
        demo_accounts = self._seed_accounts()
        demo_assets = self._seed_assets(demo_accounts)

        combined_assets = {**currency_assets, **demo_tokens, **demo_assets}
        self._seed_transfers(demo_assets, demo_accounts)
        self._seed_user_wallets(demo_accounts, combined_assets)

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
                    "system": RESERVE_SYSTEM,
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
                "system": RESERVE_SYSTEM,
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
    # Always-on: the host's gas asset, FLOR                              #
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
        # One reference per ticker: create_currency is one-shot per reference, and
        # that is the whole immutability guarantee for a fixed supply.
        reference = f"create-{ticker.lower()}"
        gas_code, gas_symbol = GAS_DISPLAY.get(ticker, ("", ticker))

        if LedgerTransaction.objects.filter(reference=reference).exists():
            gas = Asset.objects.get(unit_name=ticker)
            existing_supply = gas.max_supply_display
            if existing_supply != supply:
                self.stdout.write(self.style.WARNING(
                    f"  ⚠ {ticker} already exists with supply={existing_supply}; "
                    f"expected {supply}. Ledger immutability preserved — "
                    "update total_supply manually via a formal correction/reversal if needed."
                ))
        else:
            gas = create_currency(
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
                code=gas_code,
                symbol=gas_symbol,
            )
            gas.reserve_account = reserve
            gas.save(update_fields=["reserve_account", "updated_at"])

        gas.backing_document = "Issued and held by the platform's Currency Reserve account."
        gas.minting_authority = "Currency Reserve"
        gas.save(update_fields=["backing_document", "minting_authority", "updated_at"])
        assets[ticker] = gas
        assets["GAS"] = gas          # ticker-agnostic handle for the seeder below
        self.stdout.write(f"  +/✓ asset {ticker} (gas) supply={supply}")

        # ── FLOR, the Florin ─────────────────────────────────────────────
        if LedgerTransaction.objects.filter(reference=FLOR_REFERENCE).exists():
            flor = Asset.objects.get(unit_name="FLOR")
            existing_supply = flor.max_supply_display
            if existing_supply != FLOR_SUPPLY:
                self.stdout.write(self.style.WARNING(
                    f"  ⚠ FLOR already exists with supply={existing_supply}; "
                    f"expected {FLOR_SUPPLY}. Ledger immutability preserved — "
                    "update total_supply manually via a formal correction/reversal if needed."
                ))
        else:
            flor = create_currency(
                name="Florin",
                unit_name="FLOR",
                total_supply=FLOR_SUPPLY,
                decimals=2,
                reserve_account=reserve,
                reference=FLOR_REFERENCE,
                description=("Florin — internal accounting currency of the platform, "
                             "named after the historic gold florin; no real currency "
                             "stands behind it. 2 decimal places."),
                metadata={
                    "kind": "currency",
                    "family": "toto_currency",
                    "plural": "Florins",
                    "seeded_by": "ingress",
                },
                code=FLOR_CODE,
                symbol=FLOR_SYMBOL,
            )
            flor.reserve_account = reserve
            flor.save(update_fields=["reserve_account", "updated_at"])

        flor.backing_document = "Issued and held by the platform's Currency Reserve account."
        flor.minting_authority = "Currency Reserve"
        flor.save(update_fields=["backing_document", "minting_authority", "updated_at"])
        assets["FLOR"] = flor
        self.stdout.write(f"  +/✓ asset FLOR supply={FLOR_SUPPLY}")

        assert Asset.objects.filter(unit_name=ticker).exists()
        assert Asset.objects.filter(unit_name="FLOR").exists()
        assert gas.decimals == 9
        assert flor.decimals == 2
        # No is_currency assertion: an asset becomes a currency when a
        # platform bills in it, which is a contract, not a column.

        return assets

    # ------------------------------------------------------------------ #
    # Always-on: short names and symbols for the gas asset and FLOR       #
    # ------------------------------------------------------------------ #

    def _seed_base_currencies(self, assets: dict) -> dict:
        """Put the short name and symbol on the assets themselves.

        These used to be rows in a separate Currency table. They are fields on
        the asset now: there is one kind of thing, and what makes it a
        platform's CURRENCY is that platform's contract, not a second row.

        The short name is four capital letters and keys the asset's page
        (/assets/assets/FLOR/). It is not the ticker: the Assarion's ticker is
        ASR and its short name ASAR. A gas ticker this command does not know
        keeps the short name Asset.save derived for it.
        """
        gas = assets["GAS"]
        gas_code, gas_symbol = GAS_DISPLAY.get(gas.unit_name, (gas.code, gas.unit_name))
        specs = [
            dict(unit_name=gas.unit_name, code=gas_code, symbol=gas_symbol),
            dict(unit_name="FLOR", code=FLOR_CODE, symbol=FLOR_SYMBOL),
        ]
        seeded = {}
        for spec in specs:
            asset = assets[spec["unit_name"]]
            asset.code = spec["code"]
            asset.symbol = spec["symbol"]
            asset.save(update_fields=["code", "symbol", "updated_at"])
            seeded[spec["code"]] = asset
            self.stdout.write(f"  +/✓ short name {spec['code']} ({spec['unit_name']})")

        assert assets["GAS"].code == gas_code
        assert assets["FLOR"].code == FLOR_CODE
        return seeded

    # ------------------------------------------------------------------ #
    # Full-only: demo tokens (BANANA, MAKARONI)                          #
    # ------------------------------------------------------------------ #

    def _seed_demo_platform_assets(self) -> dict:
        """Playful per-resource tokens, for demos only.

        A real build has exactly two assets — ASR to spend and FLOR to account
        in — so these stay out of it. They exist to show that a price row can
        name any asset, not just the gas.

        Each unit is a thing on a kitchen table, never an amount of money: no
        asset of this platform is priced in, pegged to or converted through a
        real currency (economy.md, "No real currency, anywhere").
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
                    "AI inference token. 1 BANANA = 1 banana (the fruit, ~120 g). "
                    "A bunch of six ≈ 6 BANANA; a long conversation eats a few."
                ),
                metadata={
                    "kind": "platform_token",
                    "unit": "banana",
                    "unit_label": "1 token = 1 banana (~120 g)",
                    "bunch": "6 BANANA",
                    "seeded_by": "ingress",
                },
                code="BNNA",
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
                code="MACA",
            ),
        ]
        for spec in specs:
            ref = spec["reference"]
            if LedgerTransaction.objects.filter(reference=ref).exists():
                asset = Asset.objects.get(unit_name=spec["unit_name"])
                self.stdout.write(self.style.WARNING(f"  ⚠ skipped existing asset {spec['unit_name']}"))
            else:
                try:
                    asset = create_currency(**spec)
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
                asset = create_currency(**spec)
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
        flor = assets.get("FLOR")

        distributions = []
        if flor and flor.reserve_account:
            distributions += [
                dict(asset=flor, sender_account=flor.reserve_account,
                     receiver_account=accounts["alice"],
                     amount=Decimal("5000.00"), reference="dist-flor-alice-001",
                     description="Florin allocation to Alice"),
                dict(asset=flor, sender_account=flor.reserve_account,
                     receiver_account=accounts["bob"],
                     amount=Decimal("3000.00"), reference="dist-flor-bob-001",
                     description="Florin allocation to Bob"),
                dict(asset=flor, sender_account=flor.reserve_account,
                     receiver_account=accounts["carol"],
                     amount=Decimal("2000.00"), reference="dist-flor-carol-001",
                     description="Florin allocation to Carol"),
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
