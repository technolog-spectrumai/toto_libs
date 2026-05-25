"""
Management command: create_demo_tariffs

Creates a demonstration tariff with items for the four main service tokens.
Safe to run multiple times (uses get_or_create throughout).

Usage:
  python manage.py create_demo_tariffs
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

from toto.assets.models import Asset, LedgerAccount, AccountType, to_base_units
from toto.tariffs.models import BillingUnit, RoundingMode, Tariff, TariffItem, TariffStatus


class Command(BaseCommand):
    help = "Create demo tariffs for AI, storage, graph, and compute tokens."

    def handle(self, *args, **options):
        # Ensure receiving accounts exist
        rev_ai, _ = LedgerAccount.objects.get_or_create(
            code="REV-AI",
            defaults={"name": "AI Revenue", "account_type": AccountType.SYSTEM},
        )
        rev_storage, _ = LedgerAccount.objects.get_or_create(
            code="REV-STORAGE",
            defaults={"name": "Storage Revenue", "account_type": AccountType.SYSTEM},
        )
        rev_graph, _ = LedgerAccount.objects.get_or_create(
            code="REV-GRAPH",
            defaults={"name": "Graph/Neo4j Revenue", "account_type": AccountType.SYSTEM},
        )
        rev_compute, _ = LedgerAccount.objects.get_or_create(
            code="REV-COMPUTE",
            defaults={"name": "Compute Revenue", "account_type": AccountType.SYSTEM},
        )

        # Ensure service token assets exist
        ai_token, _ = Asset.objects.get_or_create(
            unit_name="AI_TOKEN",
            defaults={
                "name": "AI Usage Token",
                "decimals": 6,
                "total_supply_base_units": 10 ** 15,
                "active": True,
            },
        )
        storage_token, _ = Asset.objects.get_or_create(
            unit_name="STORAGE_TOKEN",
            defaults={
                "name": "Storage Token",
                "decimals": 6,
                "total_supply_base_units": 10 ** 15,
                "active": True,
            },
        )
        graph_token, _ = Asset.objects.get_or_create(
            unit_name="GRAPH_TOKEN",
            defaults={
                "name": "Graph (Neo4j) Token",
                "decimals": 6,
                "total_supply_base_units": 10 ** 15,
                "active": True,
            },
        )
        compute_token, _ = Asset.objects.get_or_create(
            unit_name="COMPUTE_TOKEN",
            defaults={
                "name": "Compute Token",
                "decimals": 6,
                "total_supply_base_units": 10 ** 15,
                "active": True,
            },
        )

        # ---------- Tariff 1: AI + Storage ----------
        tariff1, created = Tariff.objects.get_or_create(
            code="DEFAULT-AI-STORAGE",
            defaults={
                "name": "Default AI + Storage Tariff",
                "status": TariffStatus.ACTIVE,
                "description": "Charges AI_TOKEN for inference and STORAGE_TOKEN for file storage.",
            },
        )
        if created:
            self.stdout.write(f"  Created tariff: {tariff1.code}")
        else:
            self.stdout.write(f"  Tariff already exists: {tariff1.code}")

        items_ai = [
            ("ai.input_tokens",  ai_token,      "0.001",   BillingUnit.objects.get(slug="input_token"),  rev_ai,      1, "AI input token billing"),
            ("ai.output_tokens", ai_token,      "0.003",   BillingUnit.objects.get(slug="output_token"), rev_ai,      1, "AI output token billing"),
            ("storage.mb_hour",  storage_token, "0.00001", BillingUnit.objects.get(slug="mb_hour"),      rev_storage, 1, "File storage per MB per hour"),
        ]
        for code, asset, price, unit, recv, uq, label in items_ai:
            price_base = to_base_units(Decimal(price), asset.decimals)
            TariffItem.objects.get_or_create(
                tariff=tariff1,
                code=code,
                defaults={
                    "name": label,
                    "charged_asset": asset,
                    "price_per_unit_display": Decimal(price),
                    "price_per_unit_base_units": price_base,
                    "unit": unit,
                    "unit_quantity": Decimal(str(uq)),
                    "receiving_account": recv,
                    "rounding_mode": RoundingMode.UP,
                    "active": True,
                },
            )
            self.stdout.write(f"    item: {code} @ {price} {asset.unit_name}/{unit}")

        # ---------- Tariff 2: Neo4j Graph ----------
        tariff2, created = Tariff.objects.get_or_create(
            code="NEO4J-GRAPH",
            defaults={
                "name": "Neo4j Graph Storage Tariff",
                "status": TariffStatus.ACTIVE,
                "description": "Charges GRAPH_TOKEN per Neo4j node/relationship/second.",
            },
        )
        if created:
            self.stdout.write(f"  Created tariff: {tariff2.code}")

        items_graph = [
            ("neo4j.node_second",         graph_token, "0.00002", BillingUnit.objects.get(slug="second"),       rev_graph, 1),
            ("neo4j.relationship_second",  graph_token, "0.00001", BillingUnit.objects.get(slug="second"),       rev_graph, 1),
            ("neo4j.node",                 graph_token, "0.0001",  BillingUnit.objects.get(slug="node"),         rev_graph, 1),
            ("neo4j.relationship",         graph_token, "0.00005", BillingUnit.objects.get(slug="relationship"), rev_graph, 1),
        ]
        for code, asset, price, unit, recv, uq in items_graph:
            price_base = to_base_units(Decimal(price), asset.decimals)
            TariffItem.objects.get_or_create(
                tariff=tariff2,
                code=code,
                defaults={
                    "name": code,
                    "charged_asset": asset,
                    "price_per_unit_display": Decimal(price),
                    "price_per_unit_base_units": price_base,
                    "unit": unit,
                    "unit_quantity": Decimal(str(uq)),
                    "receiving_account": recv,
                    "rounding_mode": RoundingMode.UP,
                    "active": True,
                },
            )
            self.stdout.write(f"    item: {code} @ {price} {asset.unit_name}/{unit}")

        # ---------- Tariff 3: Compute ----------
        tariff3, created = Tariff.objects.get_or_create(
            code="COMPUTE-STANDARD",
            defaults={
                "name": "Standard Compute Tariff",
                "status": TariffStatus.ACTIVE,
                "description": "Charges COMPUTE_TOKEN per second/minute/hour of CPU time.",
            },
        )
        if created:
            self.stdout.write(f"  Created tariff: {tariff3.code}")

        items_compute = [
            ("compute.second", compute_token, "0.0001", BillingUnit.objects.get(slug="second"), rev_compute, 1),
            ("compute.minute", compute_token, "0.006",  BillingUnit.objects.get(slug="minute"), rev_compute, 1),
            ("compute.hour",   compute_token, "0.36",   BillingUnit.objects.get(slug="hour"),   rev_compute, 1),
        ]
        for code, asset, price, unit, recv, uq in items_compute:
            price_base = to_base_units(Decimal(price), asset.decimals)
            TariffItem.objects.get_or_create(
                tariff=tariff3,
                code=code,
                defaults={
                    "name": code,
                    "charged_asset": asset,
                    "price_per_unit_display": Decimal(price),
                    "price_per_unit_base_units": price_base,
                    "unit": unit,
                    "unit_quantity": Decimal(str(uq)),
                    "receiving_account": recv,
                    "rounding_mode": RoundingMode.UP,
                    "active": True,
                },
            )
            self.stdout.write(f"    item: {code} @ {price} {asset.unit_name}/{unit}")

        self.stdout.write(self.style.SUCCESS(
            "\nDemo tariffs created: DEFAULT-AI-STORAGE, NEO4J-GRAPH, COMPUTE-STANDARD"
        ))
