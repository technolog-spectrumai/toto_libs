from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from toto.assets.models import (
    AccountType,
    Asset,
    Currency,
    LedgerAccount,
    LedgerTransaction,
    Tokenization,
    TokenizationStatus,
)
from toto.assets.services.assets import (
    create_asset,
    create_obligation,
    reverse_transaction,
    transfer_asset,
)
from toto.ingress import IngressCommand
from toto.inventory.models import (
    InventorySite,
    ObjectCategory,
    ObjectType,
    RealWorldObject,
    StorageLocation,
)

# ── Bullion constants ──────────────────────────────────────────────────────
Q9 = Decimal("0.000000001")

GOLD_SILVER_RATIO = Decimal("60")
AUR_GOLD_OZ = Decimal("0.25")
ASR_PER_AUR = Decimal("1000")

GOLD_BAR_OZ = Decimal("3")
SILVER_BAR_OZ = Decimal("100")
TPLN_SUPPLY = Decimal("76658.70")

_silver_oz_per_aureus = AUR_GOLD_OZ * GOLD_SILVER_RATIO

gold_aur = (GOLD_BAR_OZ / AUR_GOLD_OZ).quantize(Q9)
gold_asr = (gold_aur * ASR_PER_AUR).quantize(Q9)
# Compute silver_asr directly from raw values to avoid rounding silver_aur first.
# Multiplying the already-rounded silver_aur × 1000 gives 6666.666667, not 6666.666666667.
silver_asr = (SILVER_BAR_OZ * ASR_PER_AUR / _silver_oz_per_aureus).quantize(Q9)
silver_aur = (silver_asr / ASR_PER_AUR).quantize(Q9)
total_aur = (gold_aur + silver_aur).quantize(Q9)
total_asr = (gold_asr + silver_asr).quantize(Q9)

assert gold_aur == Decimal("12.000000000"), gold_aur
assert gold_asr == Decimal("12000.000000000"), gold_asr
assert silver_aur == Decimal("6.666666667"), silver_aur
assert silver_asr == Decimal("6666.666666667"), silver_asr
assert total_aur == Decimal("18.666666667"), total_aur
assert total_asr == Decimal("18666.666666667"), total_asr
assert TPLN_SUPPLY == Decimal("76658.70"), TPLN_SUPPLY


class Command(IngressCommand):
    help = "Seed the asset ledger with base bullion assets and (optionally) demo assets."

    def process(self):
        self.stdout.write("🪙  Seeding assets ledger…")

        bullion_accounts = self._seed_bullion_accounts()
        bullion_assets = self._seed_base_bullion_assets(bullion_accounts)
        bullion_currencies = self._seed_base_bullion_currencies(bullion_assets)
        bullion_inventory = self._seed_base_bullion_inventory()
        self._seed_base_bullion_chests_and_tokenizations(
            accounts=bullion_accounts,
            assets=bullion_assets,
            currencies=bullion_currencies,
            inventory=bullion_inventory,
        )
        self._seed_base_platform_assets()

        if not self.full:
            self.stdout.write(self.style.SUCCESS(
                "✅  Base bullion ingress complete. Use --full for all other ingress-created assets and demo data."
            ))
            return

        # Everything below this point is full-only:
        # demo accounts, SPC/GGM/WUT, TUSD/TEUR, transfers, wallets, agreements,
        # founder obligations, contracts.
        demo_accounts = self._seed_accounts()
        demo_assets = self._seed_assets(demo_accounts)
        self._seed_demo_stablecoins(demo_accounts, demo_assets)
        contracts = self._seed_contracts()

        combined_assets = {**bullion_assets, **demo_assets}
        self._seed_transfers(demo_assets, demo_accounts)
        self._seed_user_wallets(demo_accounts, combined_assets)
        self._seed_sample_agreements(demo_accounts, contracts)
        self._seed_founder_obligations(demo_accounts, combined_assets)

        self.stdout.write(self.style.SUCCESS("✅  Asset ledger ingress complete."))

    # ------------------------------------------------------------------ #
    # Always-on: Bullion Reserve Account                                  #
    # ------------------------------------------------------------------ #

    def _seed_bullion_accounts(self) -> dict:
        acc, created = LedgerAccount.objects.get_or_create(
            code="bullion-reserve",
            defaults={
                "name": "Bullion Reserve",
                "account_type": AccountType.RESERVE,
                "active": True,
                "metadata": {
                    "kind": "precious_metal_reserve",
                    "system": "aureus_assarion_tpln",
                    "gold_silver_ratio": "60",
                    "pln_valuation": "76658.70",
                },
            },
        )
        if not created:
            fields = []
            if acc.name != "Bullion Reserve":
                acc.name = "Bullion Reserve"
                fields.append("name")
            if acc.account_type != AccountType.RESERVE:
                acc.account_type = AccountType.RESERVE
                fields.append("account_type")
            if not acc.active:
                acc.active = True
                fields.append("active")
            meta = dict(acc.metadata or {})
            for k, v in {
                "kind": "precious_metal_reserve",
                "system": "aureus_assarion_tpln",
                "gold_silver_ratio": "60",
                "pln_valuation": "76658.70",
            }.items():
                meta.setdefault(k, v)
            if meta != (acc.metadata or {}):
                acc.metadata = meta
                fields.append("metadata")
            if fields:
                acc.save(update_fields=fields)
        self.stdout.write("  +/✓ account bullion-reserve")
        return {"bullion_reserve": acc}

    # ------------------------------------------------------------------ #
    # Always-on: AUR, ASR, TPLN                                          #
    # ------------------------------------------------------------------ #

    def _seed_base_bullion_assets(self, accounts: dict) -> dict:
        reserve = accounts["bullion_reserve"]
        assets = {}

        # ── AUR ──────────────────────────────────────────────────────────
        if LedgerTransaction.objects.filter(reference="create-aur").exists():
            aur = Asset.objects.get(unit_name="AUR")
        else:
            aur = create_asset(
                name="Aureus",
                unit_name="AUR",
                total_supply=gold_aur,
                decimals=9,
                reserve_account=reserve,
                reference="create-aur",
                description="Gold-referenced currency unit. 1 AUR = 0.25 troy oz gold = 1000 ASR.",
                metadata={
                    "kind": "currency",
                    "family": "aureus_assarion",
                    "plural": "Aurei",
                    "ratio": {"AUR_TO_ASR": "1000", "ASR_PER_AUR": "1000"},
                    "metal_reference": {
                        "metal": "gold",
                        "troy_oz_gold_per_AUR": "0.25",
                        "gold_silver_ratio": "60",
                    },
                    "backing": {
                        "type": "physical_bullion",
                        "object_name": "Gold Chest — 3 oz Gold Bar",
                        "weight_troy_oz": "3",
                        "vault_reference": "VAULT-GOLD-3OZ-001",
                        "inventory_site": "Virtual Bullion Vault",
                        "storage_location": "BULLION-CHESTS",
                    },
                    "issued": {
                        "AUR": "12.000000000",
                        "ASR_equivalent": "12000.000000000",
                    },
                    "market_realistic_supply": True,
                    "seeded_by": "ingress",
                },
            )
            aur.reserve_account = reserve
            aur.save(update_fields=["reserve_account", "updated_at"])

        aur.is_currency = True
        aur.backing_document = (
            "Aureus is backed by vaulted gold inventory. Seeded supply of 12.000000000 AUR "
            "is backed by the Gold Chest — 3 oz Gold Bar. 1 AUR = 0.25 troy oz gold."
        )
        aur.minting_authority = "Bullion Reserve"
        aur.save(update_fields=["is_currency", "backing_document", "minting_authority", "updated_at"])
        assets["AUR"] = aur
        self.stdout.write(f"  +/✓ asset AUR supply={gold_aur}")

        # ── ASR ──────────────────────────────────────────────────────────
        if LedgerTransaction.objects.filter(reference="create-asr").exists():
            asr = Asset.objects.get(unit_name="ASR")
        else:
            asr = create_asset(
                name="Assarion",
                unit_name="ASR",
                total_supply=silver_asr,
                decimals=9,
                reserve_account=reserve,
                reference="create-asr",
                description="Small currency unit of the Aureus system. 1000 ASR = 1 AUR.",
                metadata={
                    "kind": "currency",
                    "family": "aureus_assarion",
                    "plural": "Assari",
                    "ratio": {"ASR_PER_AUR": "1000", "AUR_PER_ASR": "0.001"},
                    "metal_reference": {
                        "metal": "silver",
                        "troy_oz_silver_per_ASR": "0.015",
                        "gold_silver_ratio": "60",
                    },
                    "backing": {
                        "type": "physical_bullion",
                        "object_name": "Silver Chest — 100 oz Silver Bar",
                        "weight_troy_oz": "100",
                        "vault_reference": "VAULT-SILVER-100OZ-001",
                        "inventory_site": "Virtual Bullion Vault",
                        "storage_location": "BULLION-CHESTS",
                    },
                    "issued": {
                        "ASR": "6666.666666667",
                        "AUR_equivalent": "6.666666667",
                    },
                    "market_realistic_supply": True,
                    "seeded_by": "ingress",
                },
            )
            asr.reserve_account = reserve
            asr.save(update_fields=["reserve_account", "updated_at"])

        asr.is_currency = True
        asr.backing_document = (
            "Assarion is backed by vaulted silver inventory. Seeded supply of 6,666.666666667 ASR "
            "is backed by the Silver Chest — 100 oz Silver Bar at a 60:1 gold:silver ratio. "
            "1000 ASR = 1 AUR."
        )
        asr.minting_authority = "Bullion Reserve"
        asr.save(update_fields=["is_currency", "backing_document", "minting_authority", "updated_at"])
        assets["ASR"] = asr
        self.stdout.write(f"  +/✓ asset ASR supply={silver_asr}")

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
                name="Toto PLN",
                unit_name="TPLN",
                total_supply=TPLN_SUPPLY,
                decimals=2,
                reserve_account=reserve,
                reference="create-tpln",
                description=(
                    "Internal PLN stablecoin, 1:1 pegged to Polish Zloty. "
                    "Seeded supply mirrors the PLN valuation of the virtual bullion vault."
                ),
                metadata={
                    "kind": "currency",
                    "family": "toto_fiat_stablecoin",
                    "peg": {"currency": "PLN", "ratio": "1:1"},
                    "valuation_reference": {
                        "kind": "bullion_treasure_value",
                        "gold_chest": "Gold Chest — 3 oz Gold Bar",
                        "silver_chest": "Silver Chest — 100 oz Silver Bar",
                        "total_value_PLN": "76658.70",
                        "valuation_date": "2026-06-01",
                        "note": "Static ingress valuation supplied by operator.",
                    },
                    "backing": {
                        "type": "pln_accounting_stablecoin",
                        "reserve_account": "bullion-reserve",
                        "inventory_site": "Virtual Bullion Vault",
                        "storage_location": "BULLION-CHESTS",
                    },
                    "market_realistic_supply": True,
                    "seeded_by": "ingress",
                },
            )
            tpln.reserve_account = reserve
            tpln.save(update_fields=["reserve_account", "updated_at"])

        tpln.is_currency = True
        tpln.backing_document = (
            "1:1 PLN accounting stablecoin for the bullion-backed treasure valuation. "
            "Seeded supply of 76,658.70 TPLN equals the PLN valuation of the vaulted "
            "3 oz gold chest and 100 oz silver chest."
        )
        tpln.minting_authority = "Bullion Reserve"
        tpln.save(update_fields=["is_currency", "backing_document", "minting_authority", "updated_at"])
        assets["TPLN"] = tpln
        self.stdout.write(f"  +/✓ stablecoin TPLN supply={TPLN_SUPPLY}")

        assert Asset.objects.filter(unit_name="AUR").exists()
        assert Asset.objects.filter(unit_name="ASR").exists()
        assert Asset.objects.filter(unit_name="TPLN").exists()
        assert aur.decimals == 9
        assert asr.decimals == 9
        assert tpln.decimals == 2
        assert aur.is_currency is True
        assert asr.is_currency is True
        assert tpln.is_currency is True

        return assets

    # ------------------------------------------------------------------ #
    # Always-on: Currencies AUR, ASR, PLN                                #
    # ------------------------------------------------------------------ #

    def _seed_base_bullion_currencies(self, assets: dict) -> dict:
        specs = [
            dict(code="AUR", name="Aureus",      symbol="AUR", unit_name="AUR"),
            dict(code="ASR", name="Assarion",     symbol="ASR", unit_name="ASR"),
            dict(code="PLN", name="Polish Zloty", symbol="zł", unit_name="TPLN"),
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
            label = f"currency PLN → TPLN" if spec["code"] == "PLN" else f"currency {spec['code']}"
            self.stdout.write(f"  +/✓ {label}")

        aur_cur = Currency.objects.select_related("asset").get(code="AUR")
        asr_cur = Currency.objects.select_related("asset").get(code="ASR")
        pln_cur = Currency.objects.select_related("asset").get(code="PLN")
        assert aur_cur.asset_id == assets["AUR"].pk
        assert asr_cur.asset_id == assets["ASR"].pk
        assert pln_cur.asset_id == assets["TPLN"].pk

        return currencies

    # ------------------------------------------------------------------ #
    # Always-on: ObjectType, InventorySite, StorageLocation               #
    # ------------------------------------------------------------------ #

    def _seed_base_bullion_inventory(self) -> dict:
        bullion_bar_type, _ = ObjectType.objects.get_or_create(
            name="Bullion Bar",
            defaults={
                "category": ObjectCategory.COMMODITY,
                "is_mobile": False,
                "description": "Vaulted precious-metal bullion bar measured in troy ounces.",
                "metadata": {"kind": "precious_metal_bar", "unit": "troy_oz"},
            },
        )
        self.stdout.write("  +/✓ object type Bullion Bar")

        virtual_bullion_vault, _ = InventorySite.objects.update_or_create(
            name="Virtual Bullion Vault",
            defaults={
                "site_type": "virtual",
                "is_active": True,
                "is_virtual": True,
                "metadata": {
                    "kind": "bullion_vault",
                    "custody_model": "virtual_tokenization_anchor",
                    "description": "Virtual inventory site anchoring tokenized bullion chests.",
                    "contains": [
                        "Gold Chest — 3 oz Gold Bar",
                        "Silver Chest — 100 oz Silver Bar",
                    ],
                },
            },
        )
        self.stdout.write("  +/✓ inventory site Virtual Bullion Vault is_virtual=True")

        bullion_storage_location, _ = StorageLocation.objects.update_or_create(
            site=virtual_bullion_vault,
            code="BULLION-CHESTS",
            defaults={
                "name": "Bullion Chests",
                "location_type": "virtual",
                "is_active": True,
                "metadata": {
                    "kind": "bullion_chest_storage",
                    "vault_reference_prefix": "VAULT",
                },
            },
        )
        self.stdout.write("  +/✓ storage location BULLION-CHESTS")

        assert InventorySite.objects.filter(name="Virtual Bullion Vault").exists()
        assert virtual_bullion_vault.is_virtual is True
        assert virtual_bullion_vault.site_type == "virtual"
        assert StorageLocation.objects.filter(site=virtual_bullion_vault, code="BULLION-CHESTS").exists()

        return {
            "bullion_bar_type": bullion_bar_type,
            "virtual_bullion_vault": virtual_bullion_vault,
            "bullion_storage_location": bullion_storage_location,
        }

    # ------------------------------------------------------------------ #
    # Always-on: Gold/Silver chest objects and tokenizations              #
    # ------------------------------------------------------------------ #

    def _seed_base_bullion_chests_and_tokenizations(
        self,
        *,
        accounts: dict,
        assets: dict,
        currencies: dict,
        inventory: dict,
    ):
        bullion_bar_type = inventory["bullion_bar_type"]
        aur_asset = assets["AUR"]
        asr_asset = assets["ASR"]
        aur_currency = currencies["AUR"]
        asr_currency = currencies["ASR"]

        gold_chest, _ = RealWorldObject.objects.update_or_create(
            name="Gold Chest — 3 oz Gold Bar",
            defaults={
                "object_type": bullion_bar_type,
                "quantity": Decimal("3"),
                "unit": "troy_oz",
                "estimated_value": gold_aur,
                "estimated_value_currency": aur_currency,
                "description": f"Vaulted 3 troy oz gold bar backing {gold_aur} AUR.",
                "metadata": {
                    "kind": "tokenized_chest",
                    "chest_type": "gold_bar",
                    "metal": "gold",
                    "weight_troy_oz": "3",
                    "purity": "0.9999",
                    "tokenized_as": "AUR",
                    "issued_AUR": "12.000000000",
                    "issued_ASR_equivalent": "12000.000000000",
                    "gold_silver_ratio": "60",
                    "vault_reference": "VAULT-GOLD-3OZ-001",
                    "inventory_site": "Virtual Bullion Vault",
                    "inventory_site_type": "virtual",
                    "inventory_site_is_virtual": True,
                    "storage_location": "BULLION-CHESTS",
                    "market_realistic_supply": True,
                },
            },
        )
        self.stdout.write(
            "  +/✓ inventory Gold Chest — 3 oz Gold Bar in Virtual Bullion Vault / BULLION-CHESTS"
        )

        silver_chest, _ = RealWorldObject.objects.update_or_create(
            name="Silver Chest — 100 oz Silver Bar",
            defaults={
                "object_type": bullion_bar_type,
                "quantity": Decimal("100"),
                "unit": "troy_oz",
                "estimated_value": silver_asr,
                "estimated_value_currency": asr_currency,
                "description": (
                    f"Vaulted 100 troy oz silver bar backing {silver_asr} ASR "
                    "at a 60:1 gold:silver ratio."
                ),
                "metadata": {
                    "kind": "tokenized_chest",
                    "chest_type": "silver_bar",
                    "metal": "silver",
                    "weight_troy_oz": "100",
                    "purity": "0.999",
                    "tokenized_as": "ASR",
                    "issued_ASR": "6666.666666667",
                    "issued_AUR_equivalent": "6.666666667",
                    "gold_silver_ratio": "60",
                    "vault_reference": "VAULT-SILVER-100OZ-001",
                    "inventory_site": "Virtual Bullion Vault",
                    "inventory_site_type": "virtual",
                    "inventory_site_is_virtual": True,
                    "storage_location": "BULLION-CHESTS",
                    "market_realistic_supply": True,
                },
            },
        )
        self.stdout.write(
            "  +/✓ inventory Silver Chest — 100 oz Silver Bar in Virtual Bullion Vault / BULLION-CHESTS"
        )

        _gold_tok_meta = {
            "kind": "physical_bullion_tokenization",
            "metal": "gold",
            "weight_troy_oz": "3",
            "issued_amount": "12.000000000",
            "issued_asset": "AUR",
            "assari_equivalent": "12000.000000000",
            "vault_reference": "VAULT-GOLD-3OZ-001",
            "inventory_site": "Virtual Bullion Vault",
            "storage_location": "BULLION-CHESTS",
            "backing_policy": "market_realistic_supply",
        }
        gold_tok, created = Tokenization.objects.get_or_create(
            asset=aur_asset,
            defaults={
                "real_world_object": gold_chest,
                "status": TokenizationStatus.ACTIVE,
                "metadata": _gold_tok_meta,
            },
        )
        if not created:
            gold_tok.metadata = _gold_tok_meta
            gold_tok.save(update_fields=["metadata"])
        self.stdout.write("  +/✓ tokenization gold -> AUR")

        _silver_tok_meta = {
            "kind": "physical_bullion_tokenization",
            "metal": "silver",
            "weight_troy_oz": "100",
            "issued_amount": "6666.666666667",
            "issued_asset": "ASR",
            "aureus_equivalent": "6.666666667",
            "gold_silver_ratio": "60",
            "vault_reference": "VAULT-SILVER-100OZ-001",
            "inventory_site": "Virtual Bullion Vault",
            "storage_location": "BULLION-CHESTS",
            "backing_policy": "market_realistic_supply",
        }
        silver_tok, created = Tokenization.objects.get_or_create(
            asset=asr_asset,
            defaults={
                "real_world_object": silver_chest,
                "status": TokenizationStatus.ACTIVE,
                "metadata": _silver_tok_meta,
            },
        )
        if not created:
            silver_tok.metadata = _silver_tok_meta
            silver_tok.save(update_fields=["metadata"])
        self.stdout.write("  +/✓ tokenization silver -> ASR")

        assert RealWorldObject.objects.filter(name="Gold Chest — 3 oz Gold Bar").exists()
        assert RealWorldObject.objects.filter(name="Silver Chest — 100 oz Silver Bar").exists()
        gold_meta = RealWorldObject.objects.get(name="Gold Chest — 3 oz Gold Bar").metadata
        silver_meta = RealWorldObject.objects.get(name="Silver Chest — 100 oz Silver Bar").metadata
        assert gold_meta.get("inventory_site") == "Virtual Bullion Vault"
        assert gold_meta.get("storage_location") == "BULLION-CHESTS"
        assert silver_meta.get("inventory_site") == "Virtual Bullion Vault"
        assert silver_meta.get("storage_location") == "BULLION-CHESTS"
        assert Tokenization.objects.filter(asset=aur_asset).exists()
        assert Tokenization.objects.filter(asset=asr_asset).exists()

        self.stdout.write("")
        self.stdout.write("Treasure summary:")
        self.stdout.write(f"Gold chest: {GOLD_BAR_OZ} oz gold -> {gold_aur} AUR -> {gold_asr} ASR")
        self.stdout.write(f"Silver chest: {SILVER_BAR_OZ} oz silver -> {silver_aur} AUR -> {silver_asr} ASR")
        self.stdout.write(f"Total treasure: {total_aur} AUR -> {total_asr} ASR")
        self.stdout.write("")
        self.stdout.write("Fiat valuation:")
        self.stdout.write(f"Total treasure value: {TPLN_SUPPLY} PLN")
        self.stdout.write(f"Toto PLN stablecoin supply: {TPLN_SUPPLY} TPLN")
        self.stdout.write("")

    # ------------------------------------------------------------------ #
    # Always-on: STORAGE_TOKEN and COMPUTE_TOKEN                         #
    # ------------------------------------------------------------------ #

    def _seed_base_platform_assets(self) -> dict:
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
                name="Storage Token",
                unit_name="STORAGE_TOKEN",
                total_supply=Decimal("1000000000.000"),
                decimals=3,
                reserve_account=platform_reserve,
                reference="create-storage-token",
                description="Platform storage token. 1 STORAGE_TOKEN = 1 MB of storage or transfer.",
                metadata={
                    "kind": "platform_token",
                    "unit": "megabyte",
                    "unit_label": "1 token = 1 MB",
                    "seeded_by": "ingress",
                },
            ),
            dict(
                name="Compute Token",
                unit_name="COMPUTE_TOKEN",
                total_supply=Decimal("1000000000.000"),
                decimals=3,
                reserve_account=platform_reserve,
                reference="create-compute-token",
                description="Platform compute token. 1 COMPUTE_TOKEN = 1 CPU-second of compute.",
                metadata={
                    "kind": "platform_token",
                    "unit": "cpu_second",
                    "unit_label": "1 token = 1 CPU-second",
                    "seeded_by": "ingress",
                },
            ),
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
    # Full-only: Demo Stablecoins (TUSD, TEUR) and USD/EUR currencies    #
    # ------------------------------------------------------------------ #

    def _seed_demo_stablecoins(self, accounts: dict, assets: dict) -> dict:
        stablecoin_specs = [
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
        currencies = {}
        for spec in stablecoin_specs:
            ref = spec["reference"]
            backing_document = spec.pop("backing_document", "")
            minting_authority = spec.pop("minting_authority", "")
            if LedgerTransaction.objects.filter(reference=ref).exists():
                asset = Asset.objects.get(unit_name=spec["unit_name"])
                assets[spec["unit_name"]] = asset
                if not asset.is_currency:
                    asset.is_currency = True
                    asset.backing_document = backing_document
                    asset.minting_authority = minting_authority
                    asset.save(update_fields=["is_currency", "backing_document", "minting_authority"])
                self.stdout.write(self.style.WARNING(f"  ⚠ skipped existing stablecoin {spec['unit_name']}"))
            else:
                try:
                    asset = create_asset(**spec)
                    asset.is_currency = True
                    asset.backing_document = backing_document
                    asset.minting_authority = minting_authority
                    asset.save(update_fields=["is_currency", "backing_document", "minting_authority"])
                    assets[spec["unit_name"]] = asset
                    self.stdout.write(f"  + stablecoin {spec['unit_name']}")
                except Exception as exc:
                    self.stdout.write(self.style.ERROR(f"  ✗ {spec['unit_name']}: {exc}"))
                    continue

        currency_specs = [
            dict(code="USD", name="US Dollar", symbol="$", unit_name="TUSD"),
            dict(code="EUR", name="Euro",       symbol="€", unit_name="TEUR"),
        ]
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
        tusd = assets.get("TUSD")

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
        if tusd and tusd.reserve_account:
            distributions += [
                dict(asset=tusd, sender_account=tusd.reserve_account,
                     receiver_account=accounts["alice"],
                     amount=Decimal("1500.00"), reference="dist-tusd-alice-001",
                     description="TUSD allocation to Alice"),
                dict(asset=tusd, sender_account=tusd.reserve_account,
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
        tusd = assets.get("TUSD")

        funding = []
        if tpln and tpln.reserve_account:
            funding.append(dict(
                asset=tpln, sender_account=tpln.reserve_account,
                receiver_account=founder_account,
                amount=Decimal("8000.00"),
                reference="dist-tpln-founder-001",
                description="TPLN allocation to Founder",
            ))
        if tusd and tusd.reserve_account:
            funding.append(dict(
                asset=tusd, sender_account=tusd.reserve_account,
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
