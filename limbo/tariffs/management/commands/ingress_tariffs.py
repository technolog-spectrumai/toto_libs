"""
ingress_tariffs — seed demo tariffs, ledger accounts, assets, and usage records.

Run:
  python manage.py ingress_tariffs
  python manage.py ingress_tariffs --full   # also creates sample usage records

Price philosophy:
  - Minimum bill for a single action is ~0.1 token.
  - Storage: 1.0 STORAGE_TOKEN per MB transferred/uploaded.
  - Prices are intentionally integral so the token economy feels real and
    users accumulate/spend meaningful quantities during testing.
"""
import uuid
from decimal import Decimal

from django.apps import apps as django_apps
from django.db.models import Sum

from toto.assets.models import (
    AccountType,
    Asset,
    AssetHolding,
    LedgerAccount,
    to_base_units,
)
from toto.assets.queries import get_asset_balance_display
from toto.assets.services.assets import create_asset, transfer_asset
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

# Default total supply: 1 billion tokens (display units, 6 decimals)
_DEFAULT_SUPPLY = Decimal("1000000000")


def _bu(code, label, dimension=""):
    obj, _ = BillingUnit.objects.get_or_create(
        code=code,
        defaults={"label": label, "dimension": dimension, "app_label": "tariffs", "active": True},
    )
    return obj


def _metric(code, label, dimension="", app_label="tariffs", default_unit=None):
    obj, created = BillingMetric.objects.get_or_create(
        code=code,
        defaults={
            "label": label,
            "dimension": dimension,
            "app_label": app_label,
            "default_unit": default_unit,
            "active": True,
        },
    )
    if not created and obj.app_label != app_label:
        obj.app_label = app_label
        obj.save(update_fields=["app_label"])
    return obj


def _account(code, name, account_type=AccountType.SYSTEM):
    acc, _ = LedgerAccount.objects.get_or_create(
        code=code,
        defaults={"name": name, "account_type": account_type, "active": True},
    )
    return acc


def _asset(unit_name, name, decimals=6, total_supply=_DEFAULT_SUPPLY):
    reserve = _account(f"RES-{unit_name}", f"{name} Reserve", AccountType.RESERVE)

    existing = Asset.objects.filter(unit_name=unit_name).first()

    if existing is None:
        asset = create_asset(
            name=name,
            unit_name=unit_name,
            total_supply=total_supply,
            decimals=decimals,
            reserve_account=reserve,
            reference=f"ingress-create-{unit_name.lower()}",
            description=f"Ingress: initial issuance of {name}",
        )
        asset.reserve_account = reserve
        asset.save(update_fields=["reserve_account", "updated_at"])
        return asset

    asset = existing
    if not asset.reserve_account_id:
        asset.reserve_account = reserve
        asset.save(update_fields=["reserve_account", "updated_at"])

    distributed = (
        AssetHolding.objects
        .filter(asset=asset)
        .exclude(account=reserve)
        .aggregate(total=Sum("balance_base_units"))["total"] or 0
    )
    needed = asset.total_supply_base_units - distributed
    if needed > 0:
        holding, _ = AssetHolding.objects.get_or_create(
            asset=asset, account=reserve, defaults={"balance_base_units": 0}
        )
        if holding.balance_base_units < needed:
            holding.balance_base_units = needed
            holding.save(update_fields=["balance_base_units", "updated_at"])

    return asset


def _fund(account, asset, display_amount):
    """Top up account to at least display_amount tokens. Idempotent."""
    if not asset.reserve_account_id:
        return
    target = Decimal(str(display_amount))
    current = get_asset_balance_display(asset, account)
    to_add = target - current
    if to_add <= 0:
        return
    ref = f"ingress-fund-{account.code}-{asset.unit_name.lower()}-{uuid.uuid4().hex[:6]}"
    try:
        transfer_asset(
            asset=asset,
            sender_account=asset.reserve_account,
            receiver_account=account,
            amount=to_add,
            reference=ref,
            description=f"Ingress seed: fund {account.code}",
        )
    except Exception:
        pass


def _tariff(code, name, description, status=TariffStatus.ACTIVE):
    t, created = Tariff.objects.get_or_create(
        code=code,
        defaults={"name": name, "description": description, "status": status},
    )
    if not created and t.status != status:
        t.status = status
        t.save(update_fields=["status", "updated_at"])
    return t, created


def _item(tariff, metric, name, asset, price, unit, recv, uq=1, rounding=RoundingMode.UP, min_charge=0):
    price_dec = Decimal(str(price))
    price_base = to_base_units(price_dec, asset.decimals)
    item, created = TariffItem.objects.get_or_create(
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
    if not created and item.price_per_unit_base_units != price_base:
        item.price_per_unit_display = price_dec
        item.price_per_unit_base_units = price_base
        item.save(update_fields=["price_per_unit_display", "price_per_unit_base_units", "updated_at"])
    return item


class Command(IngressCommand):
    help = "Seed demo tariffs: service tokens, rate cards, revenue accounts, and sample usage."

    def process(self):
        self.stdout.write("  Seeding tariffs…")

        # ------------------------------------------------------------------ #
        # 0. Billing units                                                      #
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
        bu_image        = _bu("ocr.image",            "Image",                "ocr")
        bu_page         = _bu("doc.page",             "Page",                 "document")
        bu_compile      = _bu("tex.compile",          "Compile run",          "compute")
        bu_execution    = _bu("nb.execution",         "Notebook execution",   "compute")
        bu_task         = _bu("kanban.task",          "Task",                 "kanban")

        # ------------------------------------------------------------------ #
        # 0b. Billing metrics                                                   #
        # ------------------------------------------------------------------ #

        # — AI (app: steven) —
        m_ai_input   = _metric("ai.input_tokens",  "LLM Input Tokens",        "ai",      "steven", bu_input_token)
        m_ai_output  = _metric("ai.output_tokens", "LLM Output Tokens",       "ai",      "steven", bu_output_token)
        m_ai_req     = _metric("ai.requests",      "Inference Requests",      "ai",      "steven", bu_request)

        # — Storage (app: vault) —
        m_st_req     = _metric("storage.request",     "Storage upload request",  "storage", "vault",   bu_request)
        m_st_xfer    = _metric("storage.transfer_mb",  "Storage upload transfer", "storage", "vault",   bu_mb)
        m_st_hour    = _metric("storage.mb_hour",      "Storage MB-hour",         "storage", "vault",   bu_mb_hour)
        m_st_gb_hour = _metric("storage.gb_hour",      "Storage GB-hour",         "storage", "vault",   bu_gb_hour)

        # — Graph / Neo4j (app: ravioli) —
        m_neo_ns   = _metric("neo4j.node_second",         "Node·second",          "graph", "ravioli", bu_second)
        m_neo_rs   = _metric("neo4j.relationship_second",  "Relationship·second",  "graph", "ravioli", bu_second)
        m_neo_node = _metric("neo4j.node",                 "Node (flat)",          "graph", "ravioli", bu_node)
        m_neo_rel  = _metric("neo4j.relationship",         "Relationship (flat)",  "graph", "ravioli", bu_relationship)
        m_neo_q    = _metric("neo4j.query",                "Cypher Query",         "graph", "ravioli", bu_request)

        # — Compute (generic, used by mandragora / texlab) —
        m_cpu_s    = _metric("compute.second",    "CPU·second",    "compute", "tariffs", bu_second)
        m_cpu_m    = _metric("compute.minute",    "CPU·minute",    "compute", "tariffs", bu_minute)
        m_cpu_h    = _metric("compute.hour",      "CPU·hour",      "compute", "tariffs", bu_hour)
        m_cpu_mb_s = _metric("compute.mb_second", "RAM MB·second", "compute", "tariffs", bu_mb_second)

        # — API (generic) —
        m_api_req   = _metric("api.request",       "API Request",            "api", "tariffs", bu_request)
        m_api_batch = _metric("api.request_batch",  "API Request (per 1000)", "api", "tariffs", bu_request)
        m_api_hook  = _metric("api.webhook",        "Outbound Webhook",       "api", "tariffs", bu_request)

        # — OCR (app: ocr) —
        m_ocr_image = _metric("ocr.image",  "OCR image processed",   "ocr",      "ocr",          bu_image)
        m_ocr_page  = _metric("ocr.page",   "OCR page (in image)",   "ocr",      "ocr",          bu_page)

        # — TexLab (app: texlab) —
        m_tex_compile = _metric("texlab.compile", "LaTeX compile run",      "compute",  "texlab",       bu_compile)
        m_tex_page    = _metric("texlab.page",    "Output PDF page",        "document", "texlab",       bu_page)

        # — Notebooks / mandragora (app: mandragora) —
        m_nb_exec    = _metric("mandragora.execution",      "Notebook cell execution", "compute", "mandragora", bu_execution)
        m_nb_cpu_s   = _metric("mandragora.compute_second", "Notebook CPU·second",     "compute", "mandragora", bu_second)

        # — Transcription (app: transcription) —
        m_tr_req  = _metric("transcription.request", "Transcription job",          "ai", "transcription", bu_request)
        m_tr_sec  = _metric("transcription.second",  "Transcribed audio second",   "ai", "transcription", bu_second)

        # — Weather (app: weather) —
        m_wx_req  = _metric("weather.request", "Weather API request", "api", "weather", bu_request)

        # — Kanban / Boards (app: kanban) —
        m_kb_task = _metric("kanban.task",        "Task created",      "event", "kanban", bu_task)
        m_kb_req  = _metric("kanban.api_request", "Kanban API request","event", "kanban", bu_request)

        # — VideoMant (app: videomant) —
        bu_job    = _bu("job",               "Job",                      "event")
        m_vm_job  = _metric("videomant.job",    "VideoMant processing job",    "compute", "videomant", bu_job)
        m_vm_sec  = _metric("videomant.second", "VideoMant processing second", "compute", "videomant", bu_second)

        # ------------------------------------------------------------------ #
        # Base tariffs (always-on): one per installed heavy/Celery app.      #
        # Require STORAGE_TOKEN and COMPUTE_TOKEN from ingress_assets.        #
        # Each tariff is only created when its app is installed.             #
        # ------------------------------------------------------------------ #
        from toto.assets.models import Asset as _Asset

        storage_token  = _Asset.objects.filter(unit_name="STORAGE_TOKEN").first()
        compute_token  = _Asset.objects.filter(unit_name="COMPUTE_TOKEN").first()
        banana_token   = _Asset.objects.filter(unit_name="BANANA").first()
        makaroni_token = _Asset.objects.filter(unit_name="MAKARONI").first()

        if storage_token and compute_token:
            rev_storage_base = _account("REV-STORAGE-BASE", "Storage Base Revenue", AccountType.SYSTEM)
            rev_compute_base = _account("REV-COMPUTE-BASE", "Compute Base Revenue",  AccountType.SYSTEM)

            if django_apps.is_installed("toto.vault"):
                t_vault, _ = _tariff(
                    "FILE-STORAGE-BASE",
                    "File Storage Base Tariff",
                    "Base storage tariff using STORAGE_TOKEN. 1 token = 1 MB.",
                )
                _item(t_vault, m_st_req,     "Storage request",       storage_token, "0.1",  bu_request, rev_storage_base)
                _item(t_vault, m_st_xfer,    "Data transfer per MB",  storage_token, "1.0",  bu_mb,      rev_storage_base)
                _item(t_vault, m_st_hour,    "Storage per MB·hour",   storage_token, "0.01", bu_mb_hour, rev_storage_base)
                _item(t_vault, m_st_gb_hour, "Storage per GB·hour",   storage_token, "10.0", bu_gb_hour, rev_storage_base)
                self.stdout.write("    +/✓ tariff FILE-STORAGE-BASE (vault)")

            if django_apps.is_installed("toto.ravioli"):
                t_ravioli, _ = _tariff(
                    "NEO4J-GRAPH-BASE",
                    "Neo4j Graph Base Tariff",
                    "Base graph tariff using COMPUTE_TOKEN. 1 token = 1 CPU-second.",
                )
                _item(t_ravioli, m_neo_q,    "Cypher Query",          compute_token, "1.0",  bu_request,      rev_compute_base)
                _item(t_ravioli, m_neo_node, "Node (flat)",           compute_token, "0.1",  bu_node,         rev_compute_base)
                _item(t_ravioli, m_neo_rel,  "Relationship (flat)",   compute_token, "0.05", bu_relationship, rev_compute_base)
                self.stdout.write("    +/✓ tariff NEO4J-GRAPH-BASE (ravioli)")

                if makaroni_token:
                    rev_makaroni = _account("REV-MAKARONI", "Makaroni Revenue", AccountType.SYSTEM)
                    t_makaroni, _ = _tariff(
                        "MAKARONI-GRAPH",
                        "Makaroni Graph Tariff",
                        "Graph query tariff in MAKARONI. 1 MAKARONI = 1 dry macaroni piece (~0.5 g).",
                    )
                    _item(t_makaroni, m_neo_q,    "Cypher Query",        makaroni_token, "10", bu_request,      rev_makaroni)
                    _item(t_makaroni, m_neo_node, "Node (flat)",         makaroni_token, "2",  bu_node,         rev_makaroni)
                    _item(t_makaroni, m_neo_rel,  "Relationship (flat)", makaroni_token, "1",  bu_relationship, rev_makaroni)
                    self.stdout.write("    +/✓ tariff MAKARONI-GRAPH (ravioli)")

            if django_apps.is_installed("toto.steven") and banana_token:
                # Pricing basis: OpenAI GPT-4o × 2.70 margin, Polish banana prices.
                #
                # OpenAI GPT-4o:  $2.50/1M input,  $10.00/1M output
                # × 2.70 margin:  $6.75/1M input,  $27.00/1M output
                # × 3.95 PLN/$:  26.66 PLN/1M in, 106.65 PLN/1M out
                # ÷ 0.42 PLN/🍌:  63.48 BANANA/1M = 0.063 BANANA/1K input
                #                 253.93 BANANA/1M = 0.254 BANANA/1K output
                # Per-request overhead (~$0.005 base × 2.70 × 3.95 ÷ 0.42 ≈ 0.127 BANANA)
                rev_banana_base = _account("REV-BANANA-BASE", "Banana (AI) Base Revenue", AccountType.SYSTEM)
                t_steven, _ = _tariff(
                    "AI-INFERENCE-BASE",
                    "AI Inference Base Tariff",
                    "AI tariff in BANANA (GPT-4o pricing × 2.70 margin, PLN banana prices).",
                )
                _item(t_steven, m_ai_req,    "Inference request",  banana_token, "0.127", bu_request,      rev_banana_base)
                _item(t_steven, m_ai_input,  "LLM input tokens",   banana_token, "0.063", bu_input_token,  rev_banana_base, uq=1000)
                _item(t_steven, m_ai_output, "LLM output tokens",  banana_token, "0.254", bu_output_token, rev_banana_base, uq=1000)
                self.stdout.write("    +/✓ tariff AI-INFERENCE-BASE (steven, BANANA, GPT-4o×2.7 PLN)")

            if django_apps.is_installed("toto.manta"):
                t_videomant, _ = _tariff(
                    "VIDEOMANT-BASE",
                    "VideoMant Base Tariff",
                    "Base video processing tariff using COMPUTE_TOKEN. 1 token = 1 CPU-second.",
                )
                _item(t_videomant, m_vm_job, "Processing job",    compute_token, "60.0", bu_job,    rev_compute_base)
                _item(t_videomant, m_vm_sec, "Processing second", compute_token, "1.0",  bu_second, rev_compute_base)
                self.stdout.write("    +/✓ tariff VIDEOMANT-BASE (videomant)")

            if django_apps.is_installed("toto.texlab"):
                t_texlab_base, _ = _tariff(
                    "TEXLAB-BASE",
                    "TexLab Base Tariff",
                    "Base LaTeX compilation tariff using COMPUTE_TOKEN. 1 token = 1 CPU-second.",
                )
                _item(t_texlab_base, m_tex_compile, "LaTeX compile run", compute_token, "1.0", bu_compile, rev_compute_base)
                _item(t_texlab_base, m_tex_page,    "Output PDF page",   compute_token, "0.1", bu_page,    rev_compute_base)
                self.stdout.write("    +/✓ tariff TEXLAB-BASE (texlab)")

            if django_apps.is_installed("toto.mandragora"):
                t_notebooks_base, _ = _tariff(
                    "NOTEBOOKS-BASE",
                    "Notebooks Base Tariff",
                    "Base notebook execution tariff using COMPUTE_TOKEN. 1 token = 1 CPU-second.",
                )
                _item(t_notebooks_base, m_nb_exec,  "Cell execution", compute_token, "0.1", bu_execution, rev_compute_base)
                _item(t_notebooks_base, m_nb_cpu_s, "Compute second", compute_token, "0.1", bu_second,    rev_compute_base)
                self.stdout.write("    +/✓ tariff NOTEBOOKS-BASE (mandragora)")

            if django_apps.is_installed("toto.transcription"):
                t_transcription_base, _ = _tariff(
                    "TRANSCRIPTION-BASE",
                    "Transcription Base Tariff",
                    "Base transcription tariff using COMPUTE_TOKEN. 1 token = 1 CPU-second.",
                )
                _item(t_transcription_base, m_tr_req, "Transcription job", compute_token, "10.0", bu_request, rev_compute_base)
                _item(t_transcription_base, m_tr_sec, "Audio second",      compute_token, "0.5",  bu_second,  rev_compute_base)
                self.stdout.write("    +/✓ tariff TRANSCRIPTION-BASE (transcription)")

            if django_apps.is_installed("toto.ocr"):
                t_ocr_base, _ = _tariff(
                    "OCR-BASE",
                    "OCR Base Tariff",
                    "Base OCR tariff using COMPUTE_TOKEN. 1 token = 1 CPU-second.",
                )
                _item(t_ocr_base, m_ocr_image, "OCR image", compute_token, "0.5", bu_image, rev_compute_base)
                _item(t_ocr_base, m_ocr_page,  "OCR page",  compute_token, "0.1", bu_page,  rev_compute_base)
                self.stdout.write("    +/✓ tariff OCR-BASE (ocr)")

        else:
            self.stdout.write(self.style.WARNING(
                "  ⚠ STORAGE_TOKEN/COMPUTE_TOKEN not found — run ingress_assets first for base tariffs."
            ))

        if not self.full:
            self.stdout.write(self.style.SUCCESS(
                "Tariffs base ingress complete. Run with --full to also create full service token assets and usage records."
            ))
            return

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
        # 3. Tariffs and items  (prices inflated so min bill ≈ 0.1 token)     #
        # ------------------------------------------------------------------ #
        self.stdout.write("  [3/5] Tariffs and items…")

        # — Tariff A: AI Inference (used by steven) —
        ta, _ = _tariff(
            "AI-INFERENCE",
            "AI Inference Tariff",
            "Charges AI_TOKEN per input and output LLM token. "
            "Output tokens are priced 3× higher than input.",
        )
        _item(ta, m_ai_input,  "LLM Input Tokens",   ai_token, "1.0",  bu_input_token,  rev_ai, uq=1000)
        _item(ta, m_ai_output, "LLM Output Tokens",  ai_token, "3.0",  bu_output_token, rev_ai, uq=1000)
        _item(ta, m_ai_req,    "Inference Requests", ai_token, "0.5",  bu_request,      rev_ai)
        self.stdout.write(f"    + {ta.code}")

        # — Tariff B: File Storage (vault; 1 token per MB) —
        tb, _ = _tariff(
            "FILE-STORAGE",
            "File Storage Standard Tariff",
            "1 STORAGE_TOKEN per MB uploaded. 0.01 per MB·hour stored.",
        )
        _item(tb, m_st_req,     "Storage API Request",     storage_token, "0.1",  bu_request, rev_storage)
        _item(tb, m_st_xfer,    "Data Transfer per MB",    storage_token, "1.0",  bu_mb,      rev_storage)
        _item(tb, m_st_hour,    "Storage per MB·hour",     storage_token, "0.01", bu_mb_hour, rev_storage)
        _item(tb, m_st_gb_hour, "Storage per GB·hour",     storage_token, "10.0", bu_gb_hour, rev_storage)
        self.stdout.write(f"    + {tb.code}")

        # — Tariff C: Neo4j Graph (used by ravioli) —
        tc, _ = _tariff(
            "NEO4J-GRAPH",
            "Neo4j Graph Usage Tariff",
            "Charges GRAPH_TOKEN per node/relationship and per Cypher query.",
        )
        _item(tc, m_neo_ns,   "Node·second",          graph_token, "0.02",  bu_second,       rev_graph, min_charge=1)
        _item(tc, m_neo_rs,   "Relationship·second",  graph_token, "0.01",  bu_second,       rev_graph, min_charge=1)
        _item(tc, m_neo_node, "Node (flat)",           graph_token, "0.1",   bu_node,         rev_graph)
        _item(tc, m_neo_rel,  "Relationship (flat)",   graph_token, "0.05",  bu_relationship, rev_graph)
        _item(tc, m_neo_q,    "Cypher Query",          graph_token, "0.5",   bu_request,      rev_graph)
        self.stdout.write(f"    + {tc.code}")

        # — Tariff D: Compute (generic) —
        td, _ = _tariff(
            "COMPUTE-STANDARD",
            "Standard Compute Tariff",
            "Charges COMPUTE_TOKEN per CPU·second. 0.1 token per second.",
        )
        _item(td, m_cpu_s,    "CPU·second",    compute_token, "0.1",     bu_second,    rev_compute)
        _item(td, m_cpu_m,    "CPU·minute",    compute_token, "6.0",     bu_minute,    rev_compute)
        _item(td, m_cpu_h,    "CPU·hour",      compute_token, "360.0",   bu_hour,      rev_compute)
        _item(td, m_cpu_mb_s, "RAM MB·second", compute_token, "0.001",   bu_mb_second, rev_compute)
        self.stdout.write(f"    + {td.code}")

        # — Tariff E: API Gateway (generic) —
        te, _ = _tariff(
            "API-GATEWAY",
            "API Gateway Tariff",
            "0.1 API_TOKEN per request.",
        )
        _item(te, m_api_req,   "API Request",            api_token, "0.1",  bu_request, rev_api)
        _item(te, m_api_batch, "API Request (per 1000)", api_token, "50.0", bu_request, rev_api, uq=1000)
        _item(te, m_api_hook,  "Outbound Webhook",       api_token, "0.5",  bu_request, rev_api)
        self.stdout.write(f"    + {te.code}")

        # — Tariff F: OCR —
        tf_ocr, _ = _tariff(
            "OCR-STANDARD",
            "OCR Standard Tariff",
            "0.5 COMPUTE_TOKEN per image processed; 0.1 per page.",
        )
        _item(tf_ocr, m_ocr_image, "OCR image",      compute_token, "0.5",  bu_image,   rev_compute)
        _item(tf_ocr, m_ocr_page,  "OCR page",       compute_token, "0.1",  bu_page,    rev_compute)
        self.stdout.write(f"    + {tf_ocr.code}")

        # — Tariff G: TexLab —
        tf_tex, _ = _tariff(
            "TEXLAB-STANDARD",
            "TexLab Compile Tariff",
            "1.0 COMPUTE_TOKEN per compile run; 0.1 per output PDF page.",
        )
        _item(tf_tex, m_tex_compile, "LaTeX compile",   compute_token, "1.0", bu_compile, rev_compute)
        _item(tf_tex, m_tex_page,    "PDF page",        compute_token, "0.1", bu_page,    rev_compute)
        self.stdout.write(f"    + {tf_tex.code}")

        # — Tariff H: Notebooks (mandragora) —
        tf_nb, _ = _tariff(
            "NOTEBOOKS-STANDARD",
            "Notebook Execution Tariff",
            "0.1 COMPUTE_TOKEN per cell execution; 0.1 per CPU·second.",
        )
        _item(tf_nb, m_nb_exec,  "Cell execution",   compute_token, "0.1", bu_execution, rev_compute)
        _item(tf_nb, m_nb_cpu_s, "Compute second",   compute_token, "0.1", bu_second,    rev_compute)
        self.stdout.write(f"    + {tf_nb.code}")

        # — Tariff J: Transcription —
        tf_tr, _ = _tariff(
            "TRANSCRIPTION-STANDARD",
            "Transcription Tariff",
            "0.5 AI_TOKEN per job; 0.1 per audio second transcribed.",
        )
        _item(tf_tr, m_tr_req, "Transcription job",    ai_token, "0.5", bu_request, rev_ai)
        _item(tf_tr, m_tr_sec, "Audio second",         ai_token, "0.1", bu_second,  rev_ai)
        self.stdout.write(f"    + {tf_tr.code}")

        # — Tariff K: Weather —
        tf_wx, _ = _tariff(
            "WEATHER-STANDARD",
            "Weather API Tariff",
            "0.1 API_TOKEN per weather data request.",
        )
        _item(tf_wx, m_wx_req, "Weather request", api_token, "0.1", bu_request, rev_api)
        self.stdout.write(f"    + {tf_wx.code}")

        # — Tariff L: Kanban / Boards —
        tf_kb, _ = _tariff(
            "KANBAN-STANDARD",
            "Boards / Kanban Tariff",
            "0.1 API_TOKEN per task created or API request.",
        )
        _item(tf_kb, m_kb_task, "Task created",      api_token, "0.1", bu_task,    rev_api)
        _item(tf_kb, m_kb_req,  "Kanban API request",api_token, "0.1", bu_request, rev_api)
        self.stdout.write(f"    + {tf_kb.code}")

        # — Tariff M: Platform Bundle (draft) —
        tf_bundle, _ = _tariff(
            "PLATFORM-BUNDLE",
            "Platform Bundle Tariff (Draft)",
            "All-in bundle across AI, storage, graph, compute, API. Status: DRAFT.",
            status=TariffStatus.DRAFT,
        )
        _item(tf_bundle, m_ai_input,  "LLM Input",    ai_token,      "0.8",   bu_input_token, rev_ai,      uq=1000)
        _item(tf_bundle, m_ai_output, "LLM Output",   ai_token,      "2.5",   bu_output_token,rev_ai,      uq=1000)
        _item(tf_bundle, m_st_hour,   "Storage",      storage_token, "0.008", bu_mb_hour,     rev_storage)
        _item(tf_bundle, m_neo_q,     "Graph Query",  graph_token,   "0.4",   bu_request,     rev_graph)
        _item(tf_bundle, m_cpu_s,     "Compute",      compute_token, "0.08",  bu_second,      rev_compute)
        _item(tf_bundle, m_api_req,   "API Call",     api_token,     "0.08",  bu_request,     rev_api)
        self.stdout.write(f"    + {tf_bundle.code} (draft)")

        # ------------------------------------------------------------------ #
        # 4. Sample payer accounts and prepaid balances                        #
        # ------------------------------------------------------------------ #
        self.stdout.write("  [4/5] Payer accounts and prepaid balances…")
        payer_alice = _account("USR-ALICE",  "Alice (AI heavy user)",   AccountType.USER)
        payer_bob   = _account("USR-BOB",    "Bob (storage user)",      AccountType.USER)
        payer_carol = _account("USR-CAROL",  "Carol (API user)",        AccountType.USER)
        payer_demo  = _account("USR-DEMO",   "Demo / sandbox user",     AccountType.USER)

        _fund(payer_alice, ai_token,      "50000.0")
        _fund(payer_bob,   storage_token, "50000.0")
        _fund(payer_carol, api_token,     "50000.0")
        _fund(payer_demo,  ai_token,      "10000.0")
        _fund(payer_demo,  storage_token, "10000.0")
        _fund(payer_demo,  compute_token, "10000.0")
        _fund(payer_demo,  graph_token,   "10000.0")
        _fund(payer_demo,  api_token,     "10000.0")
        self.stdout.write("    + Alice, Bob, Carol, Demo funded")

        # Seed MAKARONI into every real user's prepaid account so they can
        # use ravioli out of the box.  1000 MAKARONI ≈ 100 Cypher queries.
        if makaroni_token:
            from django.contrib.auth import get_user_model
            from toto.assets.prepaid import get_or_create_prepaid_account
            User = get_user_model()
            seeded = 0
            for user in User.objects.filter(is_active=True):
                prepaid, _ = get_or_create_prepaid_account(user)
                _fund(prepaid, makaroni_token, "1000")
                seeded += 1
            self.stdout.write(f"    + MAKARONI seeded into {seeded} user prepaid accounts")

        # ------------------------------------------------------------------ #
        # 5. Sample usage records (posted)                                     #
        # ------------------------------------------------------------------ #
        self.stdout.write("  [5/5] Sample usage records…")

        _usage_samples = [
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
                self.stdout.write(f"    + {label}")
            except ValueError as exc:
                failed += 1
                self.stdout.write(self.style.WARNING(f"    - {label}: {exc}"))

        self.stdout.write(self.style.SUCCESS(
            f"Tariffs ingress complete — {posted} usage records posted, {failed} failed."
        ))
