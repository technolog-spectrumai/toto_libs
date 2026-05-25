"""
ingress_tariffs — seed demo tariffs, ledger accounts, assets, and usage records.

Run:
  python manage.py ingress_tariffs
  python manage.py ingress_tariffs --full   # also creates sample usage records
"""
from decimal import Decimal

from toto.assets.models import (
    AccountType,
    Asset,
    AssetHolding,
    LedgerAccount,
    LedgerTransaction,
    TransactionType,
    LedgerEntry,
    to_base_units,
)
from toto.ingress import IngressCommand
from toto.tariffs.models import (
    BillingUnit,
    RoundingMode,
    Tariff,
    TariffItem,
    TariffStatus,
    UsageRecord,
)
from toto.tariffs.services import post_usage_record


def _asset(unit_name, name, decimals=6, supply=10 ** 15):
    asset, _ = Asset.objects.get_or_create(
        unit_name=unit_name,
        defaults={
            "name": name,
            "decimals": decimals,
            "total_supply_base_units": supply,
            "active": True,
        },
    )
    return asset


def _account(code, name, account_type=AccountType.SYSTEM):
    acc, _ = LedgerAccount.objects.get_or_create(
        code=code,
        defaults={"name": name, "account_type": account_type, "active": True},
    )
    return acc


def _fund(account, asset, display_amount):
    """Directly set a holding balance (for demo purposes only)."""
    base = to_base_units(Decimal(str(display_amount)), asset.decimals)
    holding, _ = AssetHolding.objects.get_or_create(account=account, asset=asset)
    holding.balance_base_units = max(holding.balance_base_units, base)
    holding.save(update_fields=["balance_base_units", "updated_at"])
    return holding


def _tariff(code, name, description, status=TariffStatus.ACTIVE):
    t, created = Tariff.objects.get_or_create(
        code=code,
        defaults={"name": name, "description": description, "status": status},
    )
    return t, created


def _bu(slug):
    return BillingUnit.objects.get(slug=slug)


def _item(tariff, code, name, asset, price, unit, recv, uq=1, rounding=RoundingMode.UP, min_charge=0):
    price_dec = Decimal(str(price))
    price_base = to_base_units(price_dec, asset.decimals)
    item, _ = TariffItem.objects.get_or_create(
        tariff=tariff,
        code=code,
        defaults={
            "name": name,
            "charged_asset": asset,
            "price_per_unit_display": price_dec,
            "price_per_unit_base_units": price_base,
            "unit": unit,
            "unit_quantity": Decimal(str(uq)),
            "receiving_account": recv,
            "rounding_mode": rounding,
            "minimum_charge_base_units": min_charge,
            "active": True,
        },
    )
    return item


class Command(IngressCommand):
    help = "Seed demo tariffs: service tokens, rate cards, revenue accounts, and sample usage."

    def process(self):
        self.stdout.write("🧾  Seeding tariffs…")

        # ------------------------------------------------------------------ #
        # 1. Service token assets                                              #
        # ------------------------------------------------------------------ #
        self.stdout.write("  [1/5] Assets…")
        ai_token      = _asset("AI_TOKEN",      "AI Usage Token",          decimals=6)
        storage_token = _asset("STORAGE_TOKEN", "Storage Token",           decimals=6)
        graph_token   = _asset("GRAPH_TOKEN",   "Graph (Neo4j) Token",     decimals=6)
        compute_token = _asset("COMPUTE_TOKEN", "Compute Token",           decimals=6)
        api_token     = _asset("API_TOKEN",     "API Call Token",          decimals=6)

        # ------------------------------------------------------------------ #
        # 2. Revenue / receiving accounts                                      #
        # ------------------------------------------------------------------ #
        self.stdout.write("  [2/5] Revenue accounts…")
        rev_ai      = _account("REV-AI",       "AI Revenue",              AccountType.SYSTEM)
        rev_storage = _account("REV-STORAGE",  "Storage Revenue",         AccountType.SYSTEM)
        rev_graph   = _account("REV-GRAPH",    "Graph/Neo4j Revenue",     AccountType.SYSTEM)
        rev_compute = _account("REV-COMPUTE",  "Compute Revenue",         AccountType.SYSTEM)
        rev_api     = _account("REV-API",      "API Call Revenue",        AccountType.SYSTEM)

        # ------------------------------------------------------------------ #
        # 3. Tariffs and items                                                 #
        # ------------------------------------------------------------------ #
        self.stdout.write("  [3/5] Tariffs and items…")

        # — Tariff A: AI Inference
        ta, _ = _tariff(
            "AI-INFERENCE",
            "AI Inference Tariff",
            "Charges AI_TOKEN per input and output LLM token. "
            "Output tokens are priced 3× higher than input.",
        )
        _item(ta, "ai.input_tokens",  "LLM Input Tokens",  ai_token, "0.001", _bu("input_token"),  rev_ai, uq=1000)
        _item(ta, "ai.output_tokens", "LLM Output Tokens", ai_token, "0.003", _bu("output_token"), rev_ai, uq=1000)
        _item(ta, "ai.requests",      "Inference Requests",ai_token, "0.01",  _bu("request"),      rev_ai)
        self.stdout.write(f"    + {ta.code}")

        # — Tariff B: File Storage
        tb, _ = _tariff(
            "FILE-STORAGE",
            "File Storage Standard Tariff",
            "Charges STORAGE_TOKEN per MB stored per hour, with a per-file request charge.",
        )
        _item(tb, "storage.mb_hour",  "Storage per MB·hour",    storage_token, "0.00001", _bu("mb_hour"), rev_storage)
        _item(tb, "storage.gb_hour",  "Storage per GB·hour",    storage_token, "0.01",    _bu("gb_hour"), rev_storage)
        _item(tb, "storage.request",  "Storage API Request",    storage_token, "0.0001",  _bu("request"), rev_storage)
        _item(tb, "storage.transfer_mb", "Data Transfer per MB",storage_token, "0.005",   _bu("mb"),      rev_storage)
        self.stdout.write(f"    + {tb.code}")

        # — Tariff C: Neo4j Graph
        tc, _ = _tariff(
            "NEO4J-GRAPH",
            "Neo4j Graph Usage Tariff",
            "Charges GRAPH_TOKEN per node/relationship stored per second, "
            "plus a flat rate per Cypher query.",
        )
        _item(tc, "neo4j.node_second",          "Node·second",          graph_token, "0.00002", _bu("second"),       rev_graph, min_charge=1)
        _item(tc, "neo4j.relationship_second",   "Relationship·second",  graph_token, "0.00001", _bu("second"),       rev_graph, min_charge=1)
        _item(tc, "neo4j.node",                  "Node (flat)",          graph_token, "0.0001",  _bu("node"),         rev_graph)
        _item(tc, "neo4j.relationship",          "Relationship (flat)",  graph_token, "0.00005", _bu("relationship"), rev_graph)
        _item(tc, "neo4j.query",                 "Cypher Query",         graph_token, "0.001",   _bu("request"),      rev_graph)
        self.stdout.write(f"    + {tc.code}")

        # — Tariff D: Compute
        td, _ = _tariff(
            "COMPUTE-STANDARD",
            "Standard Compute Tariff",
            "Charges COMPUTE_TOKEN per CPU·second. Minute and hour items for convenience "
            "— choose whichever granularity fits the workload.",
        )
        _item(td, "compute.second",   "CPU·second",   compute_token, "0.0001",  _bu("second"),    rev_compute)
        _item(td, "compute.minute",   "CPU·minute",   compute_token, "0.006",   _bu("minute"),    rev_compute)
        _item(td, "compute.hour",     "CPU·hour",     compute_token, "0.36",    _bu("hour"),      rev_compute)
        _item(td, "compute.mb_second","RAM MB·second",compute_token, "0.000001",_bu("mb_second"), rev_compute)
        self.stdout.write(f"    + {td.code}")

        # — Tariff E: API Gateway
        te, _ = _tariff(
            "API-GATEWAY",
            "API Gateway Tariff",
            "Charges API_TOKEN per inbound API request. "
            "Priced per-1000 for high-volume endpoints.",
        )
        _item(te, "api.request",       "API Request",           api_token, "0.0001", _bu("request"), rev_api)
        _item(te, "api.request_batch", "API Request (per 1000)",api_token, "0.05",  _bu("request"), rev_api, uq=1000)
        _item(te, "api.webhook",       "Outbound Webhook",      api_token, "0.001", _bu("request"), rev_api)
        self.stdout.write(f"    + {te.code}")

        # — Tariff F: Bundled all-in (draft, for preview)
        tf, _ = _tariff(
            "PLATFORM-BUNDLE",
            "Platform Bundle Tariff (Draft)",
            "Composite tariff bundling AI, storage, graph, compute, and API charges "
            "into a single rate card. Set to DRAFT — not yet active.",
            status=TariffStatus.DRAFT,
        )
        _item(tf, "ai.input_tokens",   "LLM Input",      ai_token,      "0.0008",   _bu("input_token"),  rev_ai,      uq=1000)
        _item(tf, "ai.output_tokens",  "LLM Output",     ai_token,      "0.0025",   _bu("output_token"), rev_ai,      uq=1000)
        _item(tf, "storage.mb_hour",   "Storage",        storage_token, "0.000008", _bu("mb_hour"),      rev_storage)
        _item(tf, "neo4j.query",       "Graph Query",    graph_token,   "0.0008",   _bu("request"),      rev_graph)
        _item(tf, "compute.second",    "Compute",        compute_token, "0.00008",  _bu("second"),       rev_compute)
        _item(tf, "api.request",       "API Call",       api_token,     "0.00008",  _bu("request"),      rev_api)
        self.stdout.write(f"    + {tf.code} (draft)")

        if not self.full:
            self.stdout.write(self.style.SUCCESS(
                "✅  Tariffs ingress complete. Run with --full to also create sample usage records."
            ))
            return

        # ------------------------------------------------------------------ #
        # 4. Sample payer accounts and prepaid balances                        #
        # ------------------------------------------------------------------ #
        self.stdout.write("  [4/5] Payer accounts and prepaid balances…")
        payer_alice = _account("USR-ALICE",  "Alice (AI heavy user)",   AccountType.USER)
        payer_bob   = _account("USR-BOB",    "Bob (storage user)",      AccountType.USER)
        payer_carol = _account("USR-CAROL",  "Carol (API user)",        AccountType.USER)
        payer_demo  = _account("USR-DEMO",   "Demo / sandbox user",     AccountType.USER)

        _fund(payer_alice, ai_token,      "100.0")
        _fund(payer_bob,   storage_token, "50.0")
        _fund(payer_carol, api_token,     "25.0")
        _fund(payer_demo,  ai_token,      "10.0")
        _fund(payer_demo,  storage_token, "10.0")
        _fund(payer_demo,  compute_token, "5.0")
        _fund(payer_demo,  graph_token,   "5.0")
        _fund(payer_demo,  api_token,     "5.0")
        self.stdout.write("    + Alice, Bob, Carol, Demo funded")

        # ------------------------------------------------------------------ #
        # 5. Sample usage records (posted)                                     #
        # ------------------------------------------------------------------ #
        self.stdout.write("  [5/5] Sample usage records…")

        _usage_samples = [
            # (tariff, payer, metric, qty, unit, label)
            (ta, payer_alice, "ai.input_tokens",  Decimal("1200"), _bu("input_token"),  "Alice LLM input"),
            (ta, payer_alice, "ai.output_tokens", Decimal("400"),  _bu("output_token"), "Alice LLM output"),
            (ta, payer_alice, "ai.requests",      Decimal("1"),    _bu("request"),      "Alice single inference"),
            (ta, payer_demo,  "ai.input_tokens",  Decimal("500"),  _bu("input_token"),  "Demo LLM input"),
            (tb, payer_bob,   "storage.mb_hour",  Decimal("200"),  _bu("mb_hour"),      "Bob file storage"),
            (tb, payer_bob,   "storage.request",  Decimal("10"),   _bu("request"),      "Bob storage reads"),
            (tb, payer_demo,  "storage.mb_hour",  Decimal("50"),   _bu("mb_hour"),      "Demo storage"),
            (tc, payer_demo,  "neo4j.node_second",Decimal("1000"), _bu("second"),       "Demo graph nodes"),
            (tc, payer_demo,  "neo4j.query",      Decimal("5"),    _bu("request"),      "Demo Cypher queries"),
            (td, payer_demo,  "compute.second",   Decimal("600"),  _bu("second"),       "Demo compute 10min"),
            (te, payer_carol, "api.request",      Decimal("100"),  _bu("request"),      "Carol API calls"),
            (te, payer_demo,  "api.request",      Decimal("50"),   _bu("request"),      "Demo API calls"),
        ]

        posted = 0
        failed = 0
        for tariff, payer, metric, qty, unit, label in _usage_samples:
            ref = f"ingress-{tariff.code}-{payer.code}-{metric}"
            # Skip if already posted (idempotent)
            if UsageRecord.objects.filter(metadata__ingress_ref=ref).exists():
                continue
            record = UsageRecord.objects.create(
                tariff=tariff,
                payer_account=payer,
                metric_code=metric,
                quantity=qty,
                unit=unit,
                source_type="ingress",
                source_id="demo",
                metadata={"ingress_ref": ref, "label": label},
            )
            try:
                post_usage_record(record, reference=f"tariff-{ref}")
                posted += 1
                self.stdout.write(f"    ✓ {label}")
            except ValueError as exc:
                failed += 1
                self.stdout.write(self.style.WARNING(f"    ✗ {label}: {exc}"))

        self.stdout.write(self.style.SUCCESS(
            f"✅  Tariffs ingress complete — {posted} usage records posted, {failed} failed."
        ))
