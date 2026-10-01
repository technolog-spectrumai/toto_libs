# Ravioli — the knowledge graph app

Ravioli is the suite's door onto Neo4j. It is where a person runs a saved
Cypher query and sees the answer as a picture, searches the graph, asks an
analysis to run over it, exports a Django object into it, and inspects
what is in there.

Django app label `toto.ravioli`, shipped in the `toto-graph` package,
mounted by each host that switches it on.

---

## The one rule

**`neo4j` is imported in `connection.py` and nowhere else.**

`Neo4jClient` says so in its own docstring, and it is the reason this app
is a boundary rather than a folder. Everything that touches the graph goes
through that class, so there is one place that knows about drivers,
sessions, retries and fallback URIs — and one place to change when any of
that changes.

If you need the raw driver (the GraphRAG retrievers do), ask for it with
`Neo4jClient.driver()`. Do not construct one.

`rag.py` imports from `neo4j_graphrag`, which is a different package: the
retrieval library, not the driver. It still takes its driver from
`Neo4jClient.driver()`, so the rule is intact.

---

## What a person can do here

Six features, four of them tabs in a strip that also links to sibling apps
(Ingestor, OCR, and optionally Connectors). Most views are superuser-only
(`superuser_required`, `views.py:17`), but **four are not**: search, the
two export views, and the health probe are `@login_required` alone. Check
which you are adding to before assuming the stricter gate.

| Feature | Route | Who |
|---|---|---|
| **Knowledge Graph** — run a saved Cypher query, draw the result | `queries/unified/` | Superuser |
| **Search** — find nodes by keyword, fulltext or meaning | `search/` | Any signed-in user |
| **Graph Analysis** — run graph algorithms over the whole graph | `graph-analysis/` | Superuser |
| **History** — what has been run, and what came back | `history/` | Superuser |
| **Ask AI** — GraphRAG: a question answered from the graph | `describe/` | Superuser, and needs Steven |
| **Export / sync / prune** — move Django rows into the graph, and plan what to remove | `export/…`, `graph-sync/plan/` | Signed in (export), superuser (plans) |

"Ask AI" is gated twice, in two different ways. `BUILD_SABBIA` decides
whether the tab is *linked*; the view itself checks that `toto.sabbia`
(Steven) is actually installed and answers `503` with a sentence if it is
not. A host with the flag on and the app absent gets a clear refusal
rather than an import error.

Queries are **admin-curated**. A `CypherQuery` row is a name and a query
somebody with the admin wrote; the page runs those, and there is no
free-text Cypher box for ordinary users. That is a deliberate limit — see
the API section, which repeats it.

---

## The pieces

| Module | What it is |
|---|---|
| `connection.py` | `Neo4jClient`, the only importer of `neo4j`. Also `is_enabled()` (reads a setting) and `is_alive()` (actually runs `RETURN 1`). |
| `views.py` | Every page and JSON endpoint. Largest module here; grouped by feature, see the routes above. |
| `services/search.py` | Three keyword strategies (basic, advanced, deep/fulltext) plus the vector path, behind one `run_search()`. |
| `services/graph_plans.py` | Sync and prune **plans**: work out what would change, before anything does. |
| `vector_search.py` | Semantic search over an embedding index. |
| `rag.py` | GraphRAG: retrieve from the graph, answer with a language model. |
| `graph_analysis.py` | Runs analyses over an extracted graph. |
| `graph_export.py` | One Django object into the graph, with a preview step first. |
| `neojson.py` | The interchange format. See below. |
| `predefined_tasks.py` | Registers this app's query and analysis nodes with the workflow engine. |
| `api_views.py` | The gated JSON API Aurora consumes. |
| `models.py` | `CypherQuery` and `CypherQueryResult`. Only two — the graph itself is not in Postgres. |

`services/`, `management/commands/` and `tests/` are all real: about 2,600
lines of tests, and three commands (`ingress_ravioli`,
`ravioli_bootstrap_vector_index`, `ravioli_reindex_embeddings`).

---

## NeoJSON

`neojson.py` is a reference implementation of a small format: **as GeoJSON
is to geographic data, NeoJSON is to graph data.** One JSON object with
`nodes` and `relationships` arrays, each member self-describing through a
`type` discriminator, and a `neojson` version string at the root
(currently `1.0`).

It exists so a graph can leave this app without leaving behind the thing
that produced it. `dumps`/`loads` round-trip losslessly, `from_ravioli`
bridges what `Neo4jClient.extract_graph` produces, and
`to_networkx`/`from_networkx` bridge the analysis pipeline.

The module docstring points at `neojson_design.md` "at the repository
root". **That file does not exist** anywhere in this repository. The
docstring in `neojson.py` is the specification in practice; treat the
pointer as stale rather than as a missing dependency.

The `neo_editor` app in this same package edits NeoJSON documents.

---

## Settings

Ravioli reads these, all through `getattr` with defaults, so a host that
sets none of them still imports.

| Setting | What it decides |
|---|---|
| `RAVIOLI_ENABLED` | Whether the app's features work at all. `is_enabled()` reads only this. |
| `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` | Where the graph is and who connects. |
| `RAVIOLI_NEO4J_LOCAL_FALLBACK` | Also try `127.0.0.1` when the URI names a container host. Default on. |
| `RAVIOLI_NEO4J_PREFER_LOCAL_FALLBACK` | Try that fallback *first*. Default on. |
| `RAVIOLI_DEFAULT_MAX_HISTORY` | How much history is kept. |
| `RAVIOLI_EXPORT_EXCLUDED_APPS` | Apps whose models may not be exported into the graph. |
| `GRAPHRAG_PROVIDER` | Which model answers an "Ask AI" question. |
| `TOTO_VECTOR_NODE_LABEL`, `TOTO_VECTOR_TEXT_PROPERTY`, `TOTO_VECTOR_EMBEDDING_PROPERTY`, `TOTO_NEO4J_VECTOR_INDEX` | Where the embeddings live, for semantic search. |

`RAVIOLI_ENABLED` being true does **not** mean the graph is reachable.
That is what `is_alive()` is for, and it answers by running a real query
through the real client rather than opening a socket — so the "Neo4j is
not running" banner reflects whether graph features work, not whether
something is listening.

---

## Limits

| Value | Where | What it bounds |
|---|---|---|
| 2000 | `views.py:945` | Rows the History tab will show |
| 25, clamped 1–200 | `views.py:487`, `predefined_tasks.py:169` | Search results, clamped in both the view and the task |
| 8 | `views.py:646` | GraphRAG `top_k` — hardcoded, not user-settable |
| 20 | `rag.py:32` | Relationships gathered per node in the 1-hop expansion |
| 25 / 5 | `vector_search.py:72, 92` | Vector `top_k` for results vs. for LLM context |
| 3 | `services/graph_plans.py:21` | History snapshots kept when pruning |
| 5 | `connection.py:283` | Default depth of `get_subgraph_from_root` |

**What is deliberately unbounded:** there are no query timeouts, no Cypher
length checks, no connection-pool cap, and **no cap on history growth**.
Snapshots accumulate until somebody prunes them through the History tab —
`graph_export.py:14-16` says so in as many words, and it is a choice, not
an oversight.

---

## Things that have bitten, and why the code looks like it does

**`LIMIT $limit` refuses a string.** Every search helper writes
`int(limit)` before it hands a parameter to Cypher. Neo4j will not accept
`"25"` where it wants an integer, and it will not accept `null` either. The
casts are load-bearing, not defensive noise.

**Search says which mode it actually used.** `run_search()` returns
`requested_mode`, `effective_mode`, `fallback_used` and `fallback_reason`
alongside the results. Semantic search falls back to keyword when
embeddings are unavailable, and a fallback that looked like a successful
semantic search would be a silent lie about what the person asked for.

**Plans before writes.** Sync and prune produce a `GraphProjectionPlan`
that is reviewed and then applied. Deleting from a projection is not
reversible by re-running the projection, so the plan step is the safety.

**The API degrades rather than errors.** Listing saved queries is SQL only
and works with Neo4j down; running one returns `503` with
`{"graph_unavailable": true}` rather than a stack trace. A client can tell
"the graph is down" from "your query was wrong".

**Saved queries travel as an id, never as text.** `run_cypher_query_view`
hands the workflow engine `{"query_id": ...}` and nothing else; the query
is read from the row at execution time. `api_views.py` is the same story
and says so in its docstring. Keep it that way — a `query` string arriving
in a request body is the change to refuse.

**But GraphRAG is a genuine exception, and it is the sharpest edge here.**
`rag.py` uses `Text2CypherRetriever`, which asks a language model to
*write* Cypher and then runs it. Its module docstring is explicit:

> MVP note: `Text2CypherRetriever` (LLM → Cypher) is used directly,
> WITHOUT a read-only guard — see the plan's "Future hardening". Do not
> assume the retrieval path is non-mutable.

Repeated inline at `rag.py:172`. An LLM can emit writes or deletes against
the live graph through "Ask AI". Note that `predefined_tasks.py:206`
describes the same pipeline as "the read-only neo4j-graphrag pipeline" —
**that comment is wrong**; trust the docstring in `rag.py`. Hardening this
is the outstanding piece of work in this app.

**The projection models moved out.** `GraphChangeEvent`,
`GraphProjectionPlan`, `GraphSync` and `GraphSyncSchedule` now live in
`toto.sql_neo4j_sync`, and the SQL→Neo4j signals went with them. `models.py`
and `apps.py` both say so; if you are looking for them here, that is why
they are not.

**neomodel gets a probed URI, not the first candidate.** `neomodel_conn.py`
owns `neomodel.config.DATABASE_URL` for the whole platform and says **no
other app should set it**. The trap it documents: neomodel takes a single
URL with no fallback, while `connection_uris` puts loopback *first* for
dev convenience. Taking candidate zero would pin neomodel to `127.0.0.1`
— right on a developer's host, wrong inside a container, where every
typed-layer write fails with connection-refused against a URI nobody
configured. `_reachable_base_uri` probes instead, on first configure only,
never on the hot path. `tests/test_neomodel_conn.py` guards it.

**Bootstrapping the vector index does not fill it.**
`ravioli_bootstrap_vector_index` creates the index;
`ravioli_reindex_embeddings` is what actually embeds node text. Run only
the first and semantic search and GraphRAG return nothing, silently and
with no error. Changing the embedding provider changes the vector
dimension, so that needs a reindex with `--drop`.

**A missing workflow slug means you did not run `ingress_ravioli`.** That
command seeds the four workflows every async feature dispatches to, and
`_trigger_workflow` raises saying so (`views.py:111-114`).

**Pheromone markers are excluded from the content checksum.** A formica
deposit writing `_ph*` onto a node must not read as a content change and
trigger a spurious re-sync, so `RESERVED_PREFIXES` drops it before
hashing (`graph_export.py:40-45`).

**Cross-model foreign keys are skipped, silently.** An FK to Practitioner
declared as pointing at Person is dropped, because its uuid matches no
Person node and the edge could never be created (`graph_export.py:221`).

**A bookmarked `?mode=semantic` degrades rather than fails.** If embeddings
are gone, the URL still works and search falls back — reported honestly
through `effective_mode`, never pretended.

**`tasks.py` looks dead.** Its `run_graph_search` has no caller anywhere in
either copy of the package and uses the legacy mode names; the live search
path is the workflow in `predefined_tasks.py`. It is a *named* Celery task
(`toto.ravioli.tasks.run_graph_search`), so something outside this package
could still dispatch it by name — check that before deleting it, and do
not assume it is live before extending it.

**Metering is removed, not disabled.** `query_unified_view` hardcodes
`quota_data = []`; the old usage call raised on every render for a signed-in
user. Re-enabling needs a concrete usage/quota pair and a `metrics.py`.

**A name given to the vault is a door too.** The "Save graph as NeoJSON"
dialog stored the graph under whatever name it was given, and an analysis's
File Title named its output the same way, so `graph.docx` listed the graph's
JSON as a Word file — and the API's download named it so. Both ask the
vault's own rule, `upload_refusal(name, file_type=…)`, and answer with its
sentence: no Office extension (2026-10-01), nor a type the host refuses. The
analysis task asks it again, because a run started anywhere other than the
page never passes the page's door. `tests/test_office_names.py`, run under
`toto.ravioli.testing.settings` — the app's own harness, since no host in
the monorepo installs it:
`manage.py test toto.ravioli.tests.test_office_names --settings=toto.ravioli.testing.settings`
from a host directory.

**`predefined_tasks` failing is logged, not raised.** `apps.ready()`
catches, logs an error, and carries on — so a broken task registration
costs you the workflow nodes rather than the whole site. If workflow nodes
are missing, read the log at startup.

---

## External services

Neo4j is required for anything graph-shaped. Celery is used for the long
jobs (analysis, search, describe) and `celery_available()` is checked
rather than assumed. Semantic search additionally needs an embedding index
in Neo4j, built by `ravioli_bootstrap_vector_index` and filled by
`ravioli_reindex_embeddings`. "Ask AI" needs whatever `GRAPHRAG_PROVIDER`
names.

---

## A note on which copy you are reading

This package exists twice in the monorepo. `toto_libs/` at the repository
root is **stale** (`toto-graph` 1.30); the copy the suite actually runs is
vendored at `portal/vendor/toto_libs/` (1.50). The vendored tree is
pull-only. If you change behaviour here, check whether the live copy needs
the same change — `views.py`, `apps.py` and the graph-analysis template
already differ between the two.

The differences are small and one-way: the live copy records **who started
a run** (`WorkflowRun.started_by`, threaded through `_trigger_workflow`)
and wraps its verbose name for translation. The stale copy does neither.
Everything this file describes is true of both.
