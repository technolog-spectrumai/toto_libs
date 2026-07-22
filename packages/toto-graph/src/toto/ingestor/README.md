# toto.ingestor — text → Bento-validated graph patch

Paste arbitrary text → get a **proposed graph update** (existing-node references,
proposed new nodes, proposed relationships, with confidence, evidence and
validation) → review it as a Cytoscape **graph diff** → edit/approve/reject →
**apply the approved patch** to Neo4j.

Surfaced as the **Ingestor** tab inside Ravioli (`/ingestor/`, superuser-only).

## Constraints honored

- **Neo4j is the source of truth.** The proposal is a *patch*, never graph data.
- **Bento defines the templates.** New nodes use existing `BentoCategory`s; new
  relationships use existing `BentoEdgeType`s and their endpoint constraints.
- **Ravioli owns Neo4j.** Reads go through `bento.graph_service` (→ Ravioli's
  `Neo4jClient`); writes go through `bento.graph_service.create_node/create_edge`.
  This app opens **no** Neo4j connection of its own.
- **No LLMs. Deterministic only.** spaCy `EntityRuler` + statistical NER (fixed
  weights, deterministic inference) + rapidfuzz string distance + rule-based,
  template-constrained relationship proposal.

## Pipeline (`services/`)

`pipeline.build_proposal_dict(text)`:

1. **catalog** — read existing nodes (via `graph_service.iter_nodes`) and turn
   each category's *searchable* properties + aliases into match entries.
2. **detection** — spaCy: an `EntityRuler` built from the catalog detects
   *existing* nodes (pattern id = uid, label = category slug); the model's NER
   suggests *new* entities, mapped to categories via `INGESTOR_SPACY_LABEL_MAP`.
   Degrades to a blank pipeline (no model) — existing-entity detection + sentence
   segmentation still work.
3. **matching** — rapidfuzz (difflib fallback) flags duplicates / merge
   candidates of new entities against the catalog.
4. **relations** — for each sentence, ordered entity pairs yield only edges
   `graph_service.edge_types_between` allows; `BentoEdgeType.trigger_lemmas`
   gate/boost edges that need a verb cue.
5. **scoring** — deterministic confidence formulas (`services/scoring.py`).
6. **validation** — every node/edge checked against the live Bento template via
   `graph_service.validate_node` / `validate_edge` (also re-run at apply time).
7. **proposal** — assembled into the editable patch JSON (see below).

`apply.run(proposal_model)` writes approved + valid elements through Bento.
It is **idempotent/resumable**: created uids and applied relationship ids are
recorded in `IngestProposal.apply_result`, so a retry after a partial failure
never duplicates work.

## Proposal JSON (`IngestProposal.proposal`)

```jsonc
{
  "nodes": [{
    "temp_id": "n1", "kind": "new" | "existing",
    "category_slug": "person", "uid": null, "display": "Ada Lovelace",
    "properties": {"name": "Ada Lovelace"},
    "evidence": [{"text": "...", "start": 0, "end": 12, "sentence": 0}],
    "confidence": 0.55,
    "match": {"method": "fuzzy", "matched_uid": "...", "score": 0.91, "candidates": [...]},
    "duplicate_warning": false,
    "validation": {"status": "ok" | "error", "errors": []},
    "approval": "pending" | "approved" | "rejected",
    "merge_into_uid": null
  }],
  "relationships": [{
    "temp_id": "r1", "edge_type_slug": "works-at", "rel_type": "WORKS_AT",
    "from": "n1", "to": "n2", "properties": {},
    "evidence": [{"text": "...", "sentence": 0}],
    "confidence": 0.75, "trigger_matched": true,
    "validation": {"status": "ok", "errors": []},
    "approval": "pending"
  }]
}
```

Nodes/relationships reference each other by `temp_id`. Editing (category, props,
merge, approval, edge type) re-validates the element server-side.

## Bento template fields used

- `BentoCategory.searchable_properties` (list) — properties that identify a node
  in text; falls back to name/title/label. `alias_property` — node property
  holding alternate surface forms (list or comma string).
- `BentoEdgeType.trigger_lemmas` (list) — lemmas that propose/boost this edge.

## Settings knobs (`services/config.py`)

`INGESTOR_SPACY_MODEL` (default `en_core_web_sm`), `INGESTOR_SPACY_LABEL_MAP`,
`INGESTOR_CATALOG_MAX_NODES` (20000), `INGESTOR_DUP_THRESHOLD` (0.90),
`INGESTOR_CANDIDATE_THRESHOLD` (0.70), `INGESTOR_FUZZY_MAX_CANDIDATES` (5000).

## Deploy

`spacy` and `rapidfuzz` are in `requirements.studio.txt`. The NER model must also
be installed for new-entity suggestions:

```
python -m spacy download en_core_web_sm
```

Without it the Ingestor still runs (existing-entity detection + relationships),
but won't suggest uncategorized new entities — the review banner says so.

## Tests

`toto/toto/ingestor/tests/` — run from `portal/`:

```
BUILD_NEO4J=1 python manage.py test toto.ingestor
```

They cover Bento validators, matching, scoring, relations, proposal assembly,
the apply path (Neo4j mocked) and view permissions/flow — no live Neo4j or spaCy
model required.
