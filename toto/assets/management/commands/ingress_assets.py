from decimal import Decimal

from django.core.exceptions import ValidationError

from toto.assets.models import LedgerAccount, LedgerTransaction
from toto.assets.services.assets import create_asset, reverse_transaction, transfer_asset
from toto.ingress import IngressCommand


class Command(IngressCommand):
    help = "Seed the asset ledger with demo assets, accounts, and transactions."

    def process(self):
        self.stdout.write("🪙  Seeding assets ledger…")

        accounts = self._seed_accounts()
        assets = self._seed_assets(accounts)

        if self.full:
            self._seed_transfers(assets, accounts)

        self.stdout.write(self.style.SUCCESS("✅  Asset ledger ingress complete."))

    # ------------------------------------------------------------------ #
    # Accounts                                                             #
    # ------------------------------------------------------------------ #

    def _seed_accounts(self) -> dict:
        specs = [
            ("reserve_main",    "Main Reserve",         "reserve"),
            ("reserve_spc",     "SPC Reserve",          "reserve"),
            ("treasury",        "Treasury",             "reserve"),
            ("alice",           "Alice Vance",          "user"),
            ("bob",             "Bob Kiran",            "user"),
            ("carol",           "Carol Osei",           "user"),
            ("david",           "David Tomasz",         "user"),
            ("market_maker",    "Market Maker",         "external"),
            ("ops",             "Operations",           "system"),
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
                description="Commodity-backed token pegged to one gram of gold.",
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
                from toto.assets.models import Asset
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
    # Transfers (only with --full)                                         #
    # ------------------------------------------------------------------ #

    def _seed_transfers(self, assets: dict, accounts: dict):
        spc = assets.get("SPC")
        ggm = assets.get("GGM")
        wut = assets.get("WUT")

        transfers = []

        if spc:
            transfers += [
                # Reserve → Alice
                dict(asset=spc, sender_account=accounts["reserve_spc"],
                     receiver_account=accounts["alice"],
                     amount=Decimal("25000.00"),
                     reference="txfr-spc-reserve-alice-001",
                     description="Initial allocation to Alice"),
                # Reserve → Bob
                dict(asset=spc, sender_account=accounts["reserve_spc"],
                     receiver_account=accounts["bob"],
                     amount=Decimal("15000.00"),
                     reference="txfr-spc-reserve-bob-001",
                     description="Initial allocation to Bob"),
                # Reserve → Market Maker
                dict(asset=spc, sender_account=accounts["reserve_spc"],
                     receiver_account=accounts["market_maker"],
                     amount=Decimal("100000.00"),
                     reference="txfr-spc-reserve-mm-001",
                     description="Liquidity provision to market maker"),
                # Alice → Carol
                dict(asset=spc, sender_account=accounts["alice"],
                     receiver_account=accounts["carol"],
                     amount=Decimal("5000.00"),
                     reference="txfr-spc-alice-carol-001",
                     description="Alice sends SPC to Carol"),
                # Bob → David
                dict(asset=spc, sender_account=accounts["bob"],
                     receiver_account=accounts["david"],
                     amount=Decimal("2500.00"),
                     reference="txfr-spc-bob-david-001",
                     description="Bob sends SPC to David"),
                # Market Maker → Carol (will be reversed)
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

        # Reversal
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
