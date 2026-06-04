# toto.contracts

Contract graph editor with cryptographic signing. Provides a human-authored, visual layer on top of `assets.Contract` and `claims` primitives. Contracts are represented as labeled directed graphs (nodes + edges) rendered with Cytoscape.js, and can be signed by named persons using Ed25519 keys stored in their gervazy strongbox.

## Purpose

A lawyer or contract author creates a `Contract` document, builds a graph of nodes (obligations, entitlements, accounts, events) connected by typed edges (grants, creates_duty, triggers, settles…), and designates signatories. Each signatory unlocks their gervazy strongbox to produce a cryptographic signature; the contract is auto-executed once all required signatures are collected. A decorative handwritten signature (canvas PNG) can optionally accompany the cryptographic one.

## Models

- `Contract` — a named contract document. Fields: `uuid`, `name` (unique), `description`, `status` (`draft` / `pending` / `executed`), `metadata`, `executed_at`. `check_and_execute()` auto-sets status to `executed` when all required signatories have signed.
- `ContractNode` — a node in the contract graph. Fields: `contract` FK, `key` (slug), `node_type` (14 types), `title`, `description`, `is_manual`, `object_app/model/id` (generic FK), `position_x/y`. Unique on `(contract, key)`.
- `ContractEdge` — directed relationship between two nodes. Fields: `contract`, `source`, `target`, `edge_type` (20 types), `label`, `description`.
- `ContractSignatory` — a person required or invited to sign. Fields: `contract` FK, `person` FK, `is_required`, `signed_at`, `signature_data` (decorative PNG), `signing_payload` (canonical text signed), `cryptographic_signature` (base64 Ed25519), `signing_key` FK → `gervazy.EncryptedPrivateKey`. Unique on `(contract, person)`. Properties: `has_signed`, `is_cryptographically_signed`.

## Status lifecycle

```
draft  ──(add signatory)──▶  pending  ──(all required sign)──▶  executed
```

## Signing flow

1. Admin/author adds `ContractSignatory` records (required or optional) via the "Add Signatory" button.
2. Each signatory opens the Sign page, selects their strongbox, enters their password.
3. The view opens a `GervazyCryptoSession`, calls `gervazy.signing.SigningService.sign_document()`.
4. If the person has no signing key yet, one is provisioned automatically.
5. The Ed25519 signature over the canonical payload is stored on `ContractSignatory`.
6. When all required signatories have signed, `Contract.check_and_execute()` sets `status=executed`.

### Canonical payload

```
sign:contract
uuid:<contract.uuid>
name:<contract.name>
person:<person.pk>
at:<signed_at.isoformat()>
```

The decorative canvas signature (pencil drawing) is stored separately as a base64 PNG and is not part of what is cryptographically signed.

## Services (`services.py`)

| Function | What it does |
|---|---|
| `create_node(contract, key, node_type, ...)` | Creates or updates a `ContractNode` |
| `create_manual_node(contract, key, ...)` | Creates a manually-authored node (`is_manual=True`) |
| `create_edge(contract, source_key, target_key, edge_type, ...)` | Creates or updates a `ContractEdge` |
| `contract_to_cytoscape(contract)` | Serializes the graph to Cytoscape.js element format |

## URLs (`contracts:`)

| Name | Path | Description |
|---|---|---|
| `contract_list` | `/` | List all contracts |
| `contract_create` | `/new/` | Create contract |
| `contract_detail` | `/<uuid>/` | Detail with graph, nodes, edges, signatories |
| `contract_update` | `/<uuid>/edit/` | Edit contract |
| `contract_graph_json` | `/<uuid>/graph.json` | Cytoscape JSON endpoint |
| `contract_sign` | `/<uuid>/sign/` | Sign with strongbox password |
| `contract_add_signatory` | `/<uuid>/signatories/add/` | Add a person as signatory |
| `contract_remove_signatory` | `/<uuid>/signatories/<pk>/remove/` | Remove a signatory |
| `person_update_signature` | `/person/<pk>/signature/` | Update decorative signature |

## Key coupling

- `gervazy.EncryptedPrivateKey` ← `ContractSignatory.signing_key` — the key that produced the cryptographic signature.
- `gervazy.SigningService` — called by the sign view to produce and verify signatures.
- `people.Person` ← `ContractSignatory.person` — the signatory identity.
- Nodes can back any domain object via the `(object_app, object_model, object_id)` generic FK.

## Dependencies

- `people`
- `gervazy` (signing service + EncryptedPrivateKey FK)
