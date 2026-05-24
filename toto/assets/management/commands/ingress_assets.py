from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from toto.assets.models import Agreement, Contract, LedgerAccount, LedgerTransaction
from toto.assets.services.assets import create_asset, create_obligation, reverse_transaction, transfer_asset
from toto.ingress import IngressCommand


class Command(IngressCommand):
    help = "Seed the asset ledger with demo assets, accounts, and transactions."

    def process(self):
        self.stdout.write("🪙  Seeding assets ledger…")

        accounts = self._seed_accounts()
        assets = self._seed_assets(accounts)
        currencies = self._seed_currencies(accounts, assets)
        contracts = self._seed_contracts()

        if self.full:
            self._seed_transfers(assets, accounts)
            self._seed_user_wallets(accounts, assets)
            self._seed_sample_agreements(accounts, contracts)

        self._seed_founder_obligations(accounts, assets)
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

    # ------------------------------------------------------------------ #
    # Stablecoins & Currencies                                            #
    # ------------------------------------------------------------------ #

    def _seed_currencies(self, accounts: dict, assets: dict) -> dict:
        from toto.assets.models import Currency
        stablecoin_specs = [
            dict(
                name="Toto PLN",
                unit_name="TPLN",
                total_supply=Decimal("10000000"),
                decimals=2,
                reserve_account=accounts["reserve_main"],
                reference="create-tpln",
                description="Internal PLN stablecoin, 1:1 pegged to Polish Zloty.",
                backing_document="1:1 backed by PLN reserves held in the Toto Platform Reserve account. Redeemable at par.",
                minting_authority="Toto Platform Operations",
            ),
            dict(
                name="Toto USD",
                unit_name="TUSD",
                total_supply=Decimal("10000000"),
                decimals=2,
                reserve_account=accounts["reserve_main"],
                reference="create-tusd",
                description="Internal USD stablecoin, 1:1 pegged to US Dollar.",
                backing_document="1:1 backed by USD reserves held in the Toto Platform Reserve account. Redeemable at par.",
                minting_authority="Toto Platform Operations",
            ),
            dict(
                name="Toto EUR",
                unit_name="TEUR",
                total_supply=Decimal("5000000"),
                decimals=2,
                reserve_account=accounts["reserve_main"],
                reference="create-teur",
                description="Internal EUR stablecoin, 1:1 pegged to Euro.",
                backing_document="1:1 backed by EUR reserves held in the Toto Platform Reserve account. Redeemable at par.",
                minting_authority="Toto Platform Operations",
            ),
        ]
        for spec in stablecoin_specs:
            ref = spec["reference"]
            backing_document = spec.pop("backing_document", "")
            minting_authority = spec.pop("minting_authority", "")
            if LedgerTransaction.objects.filter(reference=ref).exists():
                from toto.assets.models import Asset
                asset = Asset.objects.get(unit_name=spec["unit_name"])
                assets[spec["unit_name"]] = asset
                # Ensure is_currency flag is set on existing assets
                if not asset.is_currency:
                    asset.is_currency = True
                    asset.backing_document = backing_document
                    asset.minting_authority = minting_authority
                    asset.save(update_fields=['is_currency', 'backing_document', 'minting_authority'])
                self.stdout.write(self.style.WARNING(f"  ⚠ skipped existing stablecoin {spec['unit_name']}"))
            else:
                try:
                    asset = create_asset(**spec)
                    asset.is_currency = True
                    asset.backing_document = backing_document
                    asset.minting_authority = minting_authority
                    asset.save(update_fields=['is_currency', 'backing_document', 'minting_authority'])
                    assets[spec["unit_name"]] = asset
                    self.stdout.write(f"  + stablecoin {spec['unit_name']}")
                except Exception as exc:
                    self.stdout.write(self.style.ERROR(f"  ✗ {spec['unit_name']}: {exc}"))
                    continue

        currency_specs = [
            dict(code="PLN", name="Polish Zloty",   symbol="zł",  unit_name="TPLN"),
            dict(code="USD", name="US Dollar",       symbol="$",   unit_name="TUSD"),
            dict(code="EUR", name="Euro",            symbol="€",   unit_name="TEUR"),
        ]
        currencies = {}
        for spec in currency_specs:
            unit = spec.pop("unit_name")
            asset = assets.get(unit)
            cur, created = Currency.objects.update_or_create(
                code=spec["code"],
                defaults={**spec, "asset": asset, "is_active": True},
            )
            currencies[spec["code"]] = cur
            if created:
                self.stdout.write(f"  + currency {spec['code']} → {unit}")
        return currencies

    # ------------------------------------------------------------------ #
    # User wallet funding (only with --full)                              #
    # ------------------------------------------------------------------ #

    def _seed_user_wallets(self, accounts: dict, assets: dict):
        """Fund user-type accounts with stablecoins for wallet demo."""
        tpln = assets.get("TPLN")
        tusd = assets.get("TUSD")

        distributions = []
        if tpln:
            distributions += [
                dict(asset=tpln, sender_account=accounts["reserve_main"],
                     receiver_account=accounts["alice"],
                     amount=Decimal("5000.00"), reference="dist-tpln-alice-001",
                     description="TPLN allocation to Alice"),
                dict(asset=tpln, sender_account=accounts["reserve_main"],
                     receiver_account=accounts["bob"],
                     amount=Decimal("3000.00"), reference="dist-tpln-bob-001",
                     description="TPLN allocation to Bob"),
                dict(asset=tpln, sender_account=accounts["reserve_main"],
                     receiver_account=accounts["carol"],
                     amount=Decimal("2000.00"), reference="dist-tpln-carol-001",
                     description="TPLN allocation to Carol"),
            ]
        if tusd:
            distributions += [
                dict(asset=tusd, sender_account=accounts["reserve_main"],
                     receiver_account=accounts["alice"],
                     amount=Decimal("1500.00"), reference="dist-tusd-alice-001",
                     description="TUSD allocation to Alice"),
                dict(asset=tusd, sender_account=accounts["reserve_main"],
                     receiver_account=accounts["bob"],
                     amount=Decimal("800.00"), reference="dist-tusd-bob-001",
                     description="TUSD allocation to Bob"),
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
    # Contracts                                                            #
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
            if Agreement.objects.filter(source_account=spec["source_account"], target_account=spec["target_account"]).exists():
                self.stdout.write(self.style.WARNING(f"  ⚠ skipped existing agreement {src}→{tgt}"))
                continue
            try:
                Agreement.objects.create(**spec)
                self.stdout.write(f"  + agreement {src}→{tgt}")
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  ✗ agreement {src}→{tgt}: {exc}"))

    # ------------------------------------------------------------------ #
    # Founder obligations (always — for testing obligation UI)            #
    # ------------------------------------------------------------------ #

    def _seed_founder_obligations(self, accounts: dict, assets: dict):
        from django.contrib.auth import get_user_model
        from toto.assets.models import Obligation

        User = get_user_model()
        admin = User.objects.filter(is_superuser=True).order_by("id").first()
        if not admin:
            self.stdout.write(self.style.WARNING("  ⚠ No superuser found — skipping founder obligations."))
            return

        # Get or create a ledger account linked to the admin user
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
        tusd = assets.get("TUSD")

        # Fund founder account so obligations are meaningful
        funding = []
        if tpln:
            funding.append(dict(
                asset=tpln, sender_account=accounts["reserve_main"],
                receiver_account=founder_account,
                amount=Decimal("8000.00"),
                reference="dist-tpln-founder-001",
                description="TPLN allocation to Founder",
            ))
        if tusd:
            funding.append(dict(
                asset=tusd, sender_account=accounts["reserve_main"],
                receiver_account=founder_account,
                amount=Decimal("3000.00"),
                reference="dist-tusd-founder-001",
                description="TUSD allocation to Founder",
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

        # Test obligations against founder account → treasury
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
        if tusd and treasury:
            obligation_specs.append(dict(
                reference="test-obligation-founder-tusd-001",
                debtor_account=founder_account,
                creditor_account=treasury,
                asset=tusd,
                amount=Decimal("100.00"),
                due_at=timezone.now() + timedelta(days=7),
                order_reference="test-fine-002",
            ))
            obligation_specs.append(dict(
                reference="test-obligation-founder-tusd-overdue-001",
                debtor_account=founder_account,
                creditor_account=treasury,
                asset=tusd,
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
