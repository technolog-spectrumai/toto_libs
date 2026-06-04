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
from toto.tariffs.models import BillingMetric, BillingUnit, RoundingMode, Tariff, TariffItem, TariffStatus


def _bu(code, label, dimension=""):
    obj, _ = BillingUnit.objects.get_or_create(
        code=code,
        defaults={"label": label, "dimension": dimension, "app_label": "tariffs", "active": True},
    )
    return obj


def _metric(code, label, dimension="", app_label="tariffs", default_unit=None):
    obj, _ = BillingMetric.objects.get_or_create(
        code=code,
        defaults={
            "label": label,
            "dimension": dimension,
            "app_label": app_label,
            "default_unit": default_unit,
            "active": True,
        },
    )
    return obj


class Command(BaseCommand):
    help = "Create demo tariffs for AI, storage, graph, and compute tokens."

    def handle(self, *args, **options):
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

        ai_token, _ = Asset.objects.get_or_create(
            unit_name="AI_TOKEN",
            defaults={"name": "AI Usage Token", "decimals": 6, "total_supply_base_units": 10 ** 15, "active": True},
        )
        storage_token, _ = Asset.objects.get_or_create(
            unit_name="STORAGE_TOKEN",
            defaults={"name": "Storage Token", "decimals": 6, "total_supply_base_units": 10 ** 15, "active": True},
        )
        graph_token, _ = Asset.objects.get_or_create(
            unit_name="GRAPH_TOKEN",
            defaults={"name": "Graph (Neo4j) Token", "decimals": 6, "total_supply_base_units": 10 ** 15, "active": True},
        )
        compute_token, _ = Asset.objects.get_or_create(
            unit_name="COMPUTE_TOKEN",
            defaults={"name": "Compute Token", "decimals": 6, "total_supply_base_units": 10 ** 15, "active": True},
        )

        bu_input_token  = _bu("ai.input_token",  "AI input token",  "ai")
        bu_output_token = _bu("ai.output_token", "AI output token", "ai")
        bu_mb_hour      = _bu("storage.mb_hour", "Megabyte hour",   "storage_time")
        bu_second       = _bu("time.second",     "Second",          "time")
        bu_node         = _bu("graph.node",      "Graph node",      "graph")
        bu_relationship = _bu("graph.relationship", "Graph relationship", "graph")
        bu_minute       = _bu("time.minute",     "Minute",          "time")
        bu_hour         = _bu("time.hour",       "Hour",            "time")
        bu_request      = _bu("request",         "Request",         "event")
        bu_mb           = _bu("storage.mb",      "Megabyte",        "storage")

        m_ai_input   = _metric("ai.input_tokens",  "LLM Input Tokens",   "ai",      "tariffs", bu_input_token)
        m_ai_output  = _metric("ai.output_tokens", "LLM Output Tokens",  "ai",      "tariffs", bu_output_token)
        m_st_hour    = _metric("storage.mb_hour",  "Storage MB-hour",    "storage", "vault",   bu_mb_hour)
        m_neo_ns     = _metric("neo4j.node_second","Node·second",        "graph",   "tariffs", bu_second)
        m_neo_rs     = _metric("neo4j.relationship_second", "Relationship·second", "graph", "tariffs", bu_second)
        m_neo_node   = _metric("neo4j.node",       "Node (flat)",        "graph",   "tariffs", bu_node)
        m_neo_rel    = _metric("neo4j.relationship","Relationship (flat)","graph",   "tariffs", bu_relationship)
        m_cpu_s      = _metric("compute.second",   "CPU·second",         "compute", "tariffs", bu_second)
        m_cpu_m      = _metric("compute.minute",   "CPU·minute",         "compute", "tariffs", bu_minute)
        m_cpu_h      = _metric("compute.hour",     "CPU·hour",           "compute", "tariffs", bu_hour)

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

        for metric, asset, price, unit, recv, uq, label in [
            (m_ai_input,  ai_token,      "0.001",   bu_input_token,  rev_ai,      1, "AI input token billing"),
            (m_ai_output, ai_token,      "0.003",   bu_output_token, rev_ai,      1, "AI output token billing"),
            (m_st_hour,   storage_token, "0.00001", bu_mb_hour,      rev_storage, 1, "File storage per MB per hour"),
        ]:
            price_base = to_base_units(Decimal(price), asset.decimals)
            TariffItem.objects.get_or_create(
                tariff=tariff1,
                metric=metric,
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
            self.stdout.write(f"    item: {metric.code} @ {price} {asset.unit_name}/{unit}")

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

        for metric, asset, price, unit, recv, uq in [
            (m_neo_ns,   graph_token, "0.00002", bu_second,       rev_graph, 1),
            (m_neo_rs,   graph_token, "0.00001", bu_second,       rev_graph, 1),
            (m_neo_node, graph_token, "0.0001",  bu_node,         rev_graph, 1),
            (m_neo_rel,  graph_token, "0.00005", bu_relationship, rev_graph, 1),
        ]:
            price_base = to_base_units(Decimal(price), asset.decimals)
            TariffItem.objects.get_or_create(
                tariff=tariff2,
                metric=metric,
                defaults={
                    "name": metric.code,
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
            self.stdout.write(f"    item: {metric.code} @ {price} {asset.unit_name}/{unit}")

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

        for metric, asset, price, unit, recv, uq in [
            (m_cpu_s, compute_token, "0.0001", bu_second, rev_compute, 1),
            (m_cpu_m, compute_token, "0.006",  bu_minute, rev_compute, 1),
            (m_cpu_h, compute_token, "0.36",   bu_hour,   rev_compute, 1),
        ]:
            price_base = to_base_units(Decimal(price), asset.decimals)
            TariffItem.objects.get_or_create(
                tariff=tariff3,
                metric=metric,
                defaults={
                    "name": metric.code,
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
            self.stdout.write(f"    item: {metric.code} @ {price} {asset.unit_name}/{unit}")

        self.stdout.write(self.style.SUCCESS(
            "\nDemo tariffs created: DEFAULT-AI-STORAGE, NEO4J-GRAPH, COMPUTE-STANDARD"
        ))
