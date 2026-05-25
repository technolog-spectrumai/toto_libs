# toto.contracts

Contract graph editor. Provides a human-authored, visual layer on top of `assets.Contract` and `claims` primitives. Contracts are represented as labeled directed graphs (nodes + edges) rendered with Cytoscape.js.

## Purpose

A lawyer or contract author creates a `Contract` document and builds a graph of nodes (obligations, entitlements, accounts, events) connected by typed edges (grants, creates_duty, triggers, settles…). Nodes can be manually authored prose or backed by real Django model instances via a generic FK. The graph is serialized to Cytoscape.js format for interactive visual editing in the browser. This layer is the human-readable face of what `assets.Contract` executes.

## Models

- `Contract` — a named contract document. Fields: `uuid`, `name` (unique), `description`, `code` (optional YAML snapshot — validated as `language: lapis`, `kind: claims_mesh`), `metadata`. The `contracts.Contract` is the visual/document layer; `assets.Contract` is the executable VM layer.
- `ContractNode` — a node in the contract graph. Fields: `contract` FK, `key` (slug, stable ID within this contract), `node_type` (14 supported types: `contract`, `obligation`, `entitlement`, `schedule`, `condition`, `allocation`, `event`, `ledger_account`, `asset`, `ledger_transaction`, `resource_type`, `agreement`, `manual`, `note`), `title`, `description`, `is_manual` (manually authored vs backed by a real object), `object_app` / `object_model` / `object_id` (generic FK to any Django model), `position_x` / `position_y` (Cytoscape layout coords). Unique on `(contract, key)`.
- `ContractEdge` — a directed relationship between two nodes. Fields: `contract`, `source` (FK to `ContractNode`), `target` (FK to `ContractNode`), `edge_type` (20 supported types including `grants`, `creates_duty`, `triggers`, `gates`, `requires`, `allocates`, `secures`, `records`, `settles`, `debtor`, `creditor`, `uses_asset`, `composes`), `label`, `description`. `clean()` validates both nodes belong to the same contract.

## Services (`services.py`)

| Function | What it does |
|---|---|
| `create_node(contract, key, node_type, ...)` | Validates type, creates `ContractNode` |
| `create_manual_node(contract, key, ...)` | Creates a node with `is_manual=True` |
| `create_edge(contract, source_key, target_key, edge_type, ...)` | Validates edge type and node membership; creates `ContractEdge` |
| `contract_to_cytoscape(contract)` | Serializes contract graph to Cytoscape.js element format for the frontend graph editor |

## Key coupling

- Nodes can back any domain object via the `(object_app, object_model, object_id)` generic FK — e.g. a node can represent a real `assets.Obligation` record.
- `ravioli` — the graph YAML exported from a contract can be imported as a Neo4j subgraph via `toto/ravioli/graph/mobilization.yaml` / `response.yaml` style manifests.

## Dependencies

None — standalone app.
