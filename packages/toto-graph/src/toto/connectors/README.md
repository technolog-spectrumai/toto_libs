# toto.connectors — external APIs → Bento-validated graph patches

Configure a **DataConnector** (which API, how to extract records, how records
map onto Bento categories/edge types) → run it (manually or on a schedule) →
every run produces an **ingestor proposal** reviewed in the Cytoscape diff UI
→ apply writes through `bento.graph_service`. Human-auditable end to end.

Surfaced as the **Connectors** tab inside Ravioli (`/connectors/`, superuser-only).

## Constraints honored

- **Neo4j is the source of truth.** A run's output is a *patch* (an
  `ingestor.IngestProposal`), never graph data.
- **Ravioli owns Neo4j.** Reads go through the ingestor catalog →
  `bento.graph_service`; writes only through `ingestor.services.apply`. This
  app opens **no** Neo4j connection of its own.
- **No LLMs. Deterministic only.** Declarative dot-path mapping + rapidfuzz
  dedupe against the live graph.
- **Secrets never live here.** Auth config/credentials belong to the
  `api.Connector` (Gervazy `EncryptedSecret` in the shared system strongbox);
  archived payloads and request logs are composed pre-auth.

## Anatomy of a run (`services/runner.execute_run`)

1. **extract** — `services/extract/rest.RestApiExtractor` fetches pages via
   `toto.api.client.execute_api_request` (auth injection, SSRF host allowlist,
   1 MiB/30 s caps; pagination: `none | page | offset | cursor`).
2. **archive** — all pages (bodies included) land as a JSON `VaultFile` in the
   `connectors-archive` bucket; an auth-free `request_log` goes on the run.
3. **transform** — `services/transform.py` maps records through the
   `mapping_spec`: within-run dedupe by (category, normalized identifier),
   graph dedupe via the ingestor catalog + rapidfuzz (exact match → an
   `existing` reference; near match → `duplicate_warning`), Bento-constrained
   relationships, and a skip of existing→existing edges already in the graph
   (`create_edge` is a Cypher CREATE — re-runs must not multiply edges).
4. **persist** — `ingestor persist_review` → proposal `ready`, run `review`.
5. **trusted auto-apply** (optional) — `ingestor.services.approval.
   approve_all_valid` (never approves validation errors) + `ingestor.services.
   apply.run` (idempotent/resumable); run ends `applied` with the full audit
   trail intact.

Run statuses: `pending → running → review | applied | empty | failed`. A human
apply through the ingestor UI flips `review → applied` via a post_save signal
on `IngestProposal`.

## `mapping_spec`

```jsonc
{
  "version": 1,
  "nodes": [{
    "rule_id": "author",                            // unique [a-z0-9_-]+
    "category_slug": "person",                      // existing BentoCategory
    "identifier": {"path": "author.display_name"},  // display + dedupe surface
    "properties": {                                 // each value EXACTLY ONE of:
      "name":   {"path": "author.display_name"},    //   dot-path ("ids.0.value" indexes lists)
      "source": {"const": "openalex"},              //   literal
      "note":   {"template": "{title} ({publication_year})"}  // "{path}" placeholders
    },
    "when": {"path": "author.display_name", "op": "truthy"}   // truthy|eq|ne|contains
  }],
  "relationships": [{
    "rule_id": "wrote",
    "edge_type_slug": "wrote",              // endpoints checked vs allowed_sources/targets
    "from_rule": "author", "to_rule": "work",  // both must fire on the same record
    "properties": {"year": {"path": "publication_year"}}
  }]
}
```

The category's identifier property (`searchable_property_names[0]`) is filled
from the identifier surface when unmapped, so required fields never come out
empty. Specs are validated structurally *and* against the live Bento templates
(`services/mapping.validate_mapping_spec`) on save and via the Validate button.

## `extract_config` (kind `rest_api`)

```jsonc
{
  "endpoint": "works",                 // relative to http_connector.base_url
  "method": "GET",                     // GET | POST
  "params": {"filter": "is_oa:true"},  // static, non-secret
  "headers": {},
  "records_path": "results",           // dot-path to the list; "" = body itself
  "pagination": {"strategy": "page", "param": "page", "start": 1, "max_pages": 10},
  "max_records": 1000,
  "timeout_seconds": 30
}
```

## Scheduling

`schedule_enabled` + `interval_minutes` per connector. A single global beat
task (`toto.connectors.tasks.connectors_scan_schedules`, every
`CONNECTORS_SCAN_MINUTES` min) claims due connectors under a row lock —
`next_run_at` advances before dispatch, in-flight runs are never stacked, and
an untrusted connector with a proposal still awaiting review is skipped (no
duplicate-proposal pileup). Runs stuck pending/running beyond
`STALE_RUN_MAX_AGE` (2 h — a killed worker) stop blocking their connector.
Deployments get a dedicated `celery_beat` container (see deploy.py).

## Deploy / flags

- `BUILD_CONNECTORS=1` (implies `BUILD_GRAPH`) — INSTALLED_APPS, `/connectors/`
  URLs, the Ravioli tab, the beat entry and ingress registration all follow it.
- Authenticated connectors need the shared system strongbox:
  `SABBIA_VAULT_PASSWORD` set (web **and** worker) + `manage.py
  connectors_init_vault` (ingress does this when the password is present).
  API keys are entered on the `api.Connector` admin form, never in configs.
- `manage.py ingress_connectors --full` seeds a no-auth **OpenAlex demo**
  mapped onto the seeded `note`/`concept` templates.

## Known limitations (v1)

- Property **updates** on existing nodes are out of scope: an exact-matched
  node becomes a reference with `properties: {}`.
- No HTTP retry/backoff/rate limiting (matches `toto.api.client`); a flaky
  upstream fails the run visibly and the next scheduled tick retries.
- The graph catalog is rebuilt per run and capped at
  `INGESTOR_CATALOG_MAX_NODES`; truncation is surfaced in run stats.

## Tests

```
cd portal && BUILD_NEO4J=1 BUILD_CONNECTORS=1 python manage.py test toto.connectors toto.ingestor
```

No live Neo4j or network required: HTTP is patched at `execute_api_request`,
graph writes at `graph_service.create_node/create_edge`, catalog at
`build_catalog` — the same seams the ingestor suite uses.
