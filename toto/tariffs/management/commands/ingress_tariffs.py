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
    BillingMetric,
    BillingUnit,
    RoundingMode,
    Tariff,
    TariffItem,
    TariffStatus,
    UsageRecord,
)
from toto.tariffs.services import post_usage_record


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


def _item(tariff, metric, name, asset, price, unit, recv, uq=1, rounding=RoundingMode.UP, min_charge=0):
    price_dec = Decimal(str(price))
    price_base = to_base_units(price_dec, asset.decimals)
    item, _ = TariffItem.objects.get_or_create(
        tariff=tariff,
        metric=metric,
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
        # 0. Billing units (owned by tariffs app)                              #
        # ------------------------------------------------------------------ #
        bu_input_token  = _bu("ai.input_token",       "AI input token",       "ai")
        bu_output_token = _bu("ai.output_token",      "AI output token",      "ai")
        bu_request      = _bu("request",              "Request",              "event")
        bu_second       = _bu("time.second",          "Second",               "time")
        bu_minute       = _bu("time.minute",          "Minute",               "time")
        bu_hour         = _bu("time.hour",            "Hour",                 "time")
        bu_node         = _bu("graph.node",           "Graph node",           "graph")
        bu_relationship = _bu("graph.relationship",   "Graph relationship",   "graph")
        bu_mb_hour      = _bu("storage.mb_hour",      "Megabyte hour",        "storage_time")
        bu_gb_hour      = _bu("storage.gb_hour",      "Gigabyte hour",        "storage_time")
        bu_mb           = _bu("storage.mb",           "Megabyte",             "storage")
        bu_mb_second    = _bu("storage.mb_second",    "Megabyte second",      "storage_time")

        # ------------------------------------------------------------------ #
        # 0b. Billing metrics (each maps a metric code to a first-class object) #
        # ------------------------------------------------------------------ #

        # AI metrics
        m_ai_input   = _metric("ai.input_tokens",  "LLM Input Tokens",        "ai",      "tariffs", bu_input_token)
        m_ai_output  = _metric("ai.output_tokens", "LLM Output Tokens",       "ai",      "tariffs", bu_output_token)
        m_ai_req     = _metric("ai.requests",      "Inference Requests",      "ai",      "tariffs", bu_request)

        # Storage metrics — owned by vault, emitted as stable string codes
        m_st_req     = _metric("storage.request",     "Storage upload request",  "storage", "vault",   bu_request)
        m_st_xfer    = _metric("storage.transfer_mb",  "Storage upload transfer", "storage", "vault",   bu_mb)
        m_st_hour    = _metric("storage.mb_hour",      "Storage MB-hour",         "storage", "vault",   bu_mb_hour)
        m_st_gb_hour = _metric("storage.gb_hour",      "Storage GB-hour",         "storage", "vault",   bu_gb_hour)

        # Graph metrics
        m_neo_ns   = _metric("neo4j.node_second",         "Node·second",          "graph", "tariffs", bu_second)
        m_neo_rs   = _metric("neo4j.relationship_second",  "Relationship·second",  "graph", "tariffs", bu_second)
        m_neo_node = _metric("neo4j.node",                 "Node (flat)",          "graph", "tariffs", bu_node)
        m_neo_rel  = _metric("neo4j.relationship",         "Relationship (flat)",  "graph", "tariffs", bu_relationship)
        m_neo_q    = _metric("neo4j.query",                "Cypher Query",         "graph", "tariffs", bu_request)

        # Compute metrics
        m_cpu_s    = _metric("compute.second",    "CPU·second",    "compute", "tariffs", bu_second)
        m_cpu_m    = _metric("compute.minute",    "CPU·minute",    "compute", "tariffs", bu_minute)
        m_cpu_h    = _metric("compute.hour",      "CPU·hour",      "compute", "tariffs", bu_hour)
        m_cpu_mb_s = _metric("compute.mb_second", "RAM MB·second", "compute", "tariffs", bu_mb_second)

        # API metrics
        m_api_req   = _metric("api.request",       "API Request",            "api", "tariffs", bu_request)
        m_api_batch = _metric("api.request_batch",  "API Request (per 1000)", "api", "tariffs", bu_request)
        m_api_hook  = _metric("api.webhook",        "Outbound Webhook",       "api", "tariffs", bu_request)

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
        _item(ta, m_ai_input,  "LLM Input Tokens",   ai_token, "0.001", bu_input_token,  rev_ai, uq=1000)
        _item(ta, m_ai_output, "LLM Output Tokens",  ai_token, "0.003", bu_output_token, rev_ai, uq=1000)
        _item(ta, m_ai_req,    "Inference Requests", ai_token, "0.01",  bu_request,      rev_ai)
        self.stdout.write(f"    + {ta.code}")

        # — Tariff B: File Storage (vault billing emits these three metric codes)
        tb, _ = _tariff(
            "FILE-STORAGE",
            "File Storage Standard Tariff",
            "Charges STORAGE_TOKEN per MB stored per hour, with a per-file request charge.",
        )
        _item(tb, m_st_req,     "Storage API Request",     storage_token, "0.0001",  bu_request, rev_storage)
        _item(tb, m_st_xfer,    "Data Transfer per MB",    storage_token, "0.005",   bu_mb,      rev_storage)
        _item(tb, m_st_hour,    "Storage per MB·hour",     storage_token, "0.00001", bu_mb_hour, rev_storage)
        _item(tb, m_st_gb_hour, "Storage per GB·hour",     storage_token, "0.01",    bu_gb_hour, rev_storage)
        self.stdout.write(f"    + {tb.code}")

        # — Tariff C: Neo4j Graph
        tc, _ = _tariff(
            "NEO4J-GRAPH",
            "Neo4j Graph Usage Tariff",
            "Charges GRAPH_TOKEN per node/relationship stored per second, "
            "plus a flat rate per Cypher query.",
        )
        _item(tc, m_neo_ns,   "Node·second",          graph_token, "0.00002", bu_second,       rev_graph, min_charge=1)
        _item(tc, m_neo_rs,   "Relationship·second",  graph_token, "0.00001", bu_second,       rev_graph, min_charge=1)
        _item(tc, m_neo_node, "Node (flat)",           graph_token, "0.0001",  bu_node,         rev_graph)
        _item(tc, m_neo_rel,  "Relationship (flat)",   graph_token, "0.00005", bu_relationship, rev_graph)
        _item(tc, m_neo_q,    "Cypher Query",          graph_token, "0.001",   bu_request,      rev_graph)
        self.stdout.write(f"    + {tc.code}")

        # — Tariff D: Compute
        td, _ = _tariff(
            "COMPUTE-STANDARD",
            "Standard Compute Tariff",
            "Charges COMPUTE_TOKEN per CPU·second. Minute and hour items for convenience "
            "— choose whichever granularity fits the workload.",
        )
        _item(td, m_cpu_s,    "CPU·second",    compute_token, "0.0001",   bu_second,    rev_compute)
        _item(td, m_cpu_m,    "CPU·minute",    compute_token, "0.006",    bu_minute,    rev_compute)
        _item(td, m_cpu_h,    "CPU·hour",      compute_token, "0.36",     bu_hour,      rev_compute)
        _item(td, m_cpu_mb_s, "RAM MB·second", compute_token, "0.000001", bu_mb_second, rev_compute)
        self.stdout.write(f"    + {td.code}")

        # — Tariff E: API Gateway
        te, _ = _tariff(
            "API-GATEWAY",
            "API Gateway Tariff",
            "Charges API_TOKEN per inbound API request. "
            "Priced per-1000 for high-volume endpoints.",
        )
        _item(te, m_api_req,   "API Request",            api_token, "0.0001", bu_request, rev_api)
        _item(te, m_api_batch, "API Request (per 1000)", api_token, "0.05",   bu_request, rev_api, uq=1000)
        _item(te, m_api_hook,  "Outbound Webhook",       api_token, "0.001",  bu_request, rev_api)
        self.stdout.write(f"    + {te.code}")

        # — Tariff F: Bundled all-in (draft, for preview)
        tf, _ = _tariff(
            "PLATFORM-BUNDLE",
            "Platform Bundle Tariff (Draft)",
            "Composite tariff bundling AI, storage, graph, compute, and API charges "
            "into a single rate card. Set to DRAFT — not yet active.",
            status=TariffStatus.DRAFT,
        )
        _item(tf, m_ai_input,  "LLM Input",    ai_token,      "0.0008",   bu_input_token, rev_ai,      uq=1000)
        _item(tf, m_ai_output, "LLM Output",   ai_token,      "0.0025",   bu_output_token,rev_ai,      uq=1000)
        _item(tf, m_st_hour,   "Storage",      storage_token, "0.000008", bu_mb_hour,     rev_storage)
        _item(tf, m_neo_q,     "Graph Query",  graph_token,   "0.0008",   bu_request,     rev_graph)
        _item(tf, m_cpu_s,     "Compute",      compute_token, "0.00008",  bu_second,      rev_compute)
        _item(tf, m_api_req,   "API Call",     api_token,     "0.00008",  bu_request,     rev_api)
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
            # (tariff, payer, metric_code, qty, unit_code, label)
            (ta, payer_alice, "ai.input_tokens",  Decimal("1200"), bu_input_token.code,  "Alice LLM input"),
            (ta, payer_alice, "ai.output_tokens", Decimal("400"),  bu_output_token.code, "Alice LLM output"),
            (ta, payer_alice, "ai.requests",      Decimal("1"),    bu_request.code,      "Alice single inference"),
            (ta, payer_demo,  "ai.input_tokens",  Decimal("500"),  bu_input_token.code,  "Demo LLM input"),
            (tb, payer_bob,   "storage.mb_hour",  Decimal("200"),  bu_mb_hour.code,      "Bob file storage"),
            (tb, payer_bob,   "storage.request",  Decimal("10"),   bu_request.code,      "Bob storage reads"),
            (tb, payer_demo,  "storage.mb_hour",  Decimal("50"),   bu_mb_hour.code,      "Demo storage"),
            (tc, payer_demo,  "neo4j.node_second",Decimal("1000"), bu_second.code,       "Demo graph nodes"),
            (tc, payer_demo,  "neo4j.query",      Decimal("5"),    bu_request.code,      "Demo Cypher queries"),
            (td, payer_demo,  "compute.second",   Decimal("600"),  bu_second.code,       "Demo compute 10min"),
            (te, payer_carol, "api.request",      Decimal("100"),  bu_request.code,      "Carol API calls"),
            (te, payer_demo,  "api.request",      Decimal("50"),   bu_request.code,      "Demo API calls"),
        ]

        posted = 0
        failed = 0
        for tariff, payer, metric, qty, unit, label in _usage_samples:
            ref = f"ingress-{tariff.code}-{payer.code}-{metric}"
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
